"""
TrendDetectionWorker — reactive слой для трендов.

Context7: подписка на posts.indexed, обновление Redis тайм-серий,
кластеризация в Qdrant и публикация events `trends.emerging`.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple, Set

import asyncpg
import httpx
import structlog
from prometheus_client import Counter, Histogram

from event_bus import EventConsumer, RedisStreamsClient, EventPublisher, ConsumerConfig
from event_bus import STREAMS  # noqa: F401 (validate presence)
from integrations.qdrant_client import QdrantClient
from ai_providers.gigachain_adapter import create_gigachain_adapter
from ai_providers.embedding_service import create_embedding_service, EmbeddingService
from config import settings
from api.worker.trends_coherence_agent import create_coherence_agent, TrendCoherenceAgent
from api.worker.trends_graph_validator import GraphClusterValidator
from api.worker.trends_drift_detector import DriftDetectorAgent
from api.worker.trends_keyword_extractor import create_keyword_extractor, TrendKeywordExtractor
from api.services.graph_service import get_graph_service
from events.schemas import TrendEmergingEventV1
from shared.trends import TrendRedisSchema, TrendWindow, TRENDS_EMERGING_STREAM

logger = structlog.get_logger()

DEFAULT_TREND_STOPWORDS = {
    "можно",
    "тащусь",
    "рублей",
    "сервис",
    "крупнейший",
    "мужчина",
    "женщина",
    "первый",
    "просто",
    "очень",
    "сегодня",
}

# Общие стоп-слова/мусорные токены (RU+EN), усиливаем фильтрацию однословных «трендов»
EXPANDED_STOPWORDS = {
    "это", "как", "так", "его", "еще", "уже", "ли", "или", "для", "при", "без",
    "по", "во", "на", "в", "и", "а", "но", "же", "то", "не", "ни", "да",
    "к", "ко", "из", "под", "над", "от", "до", "если", "то", "чтобы",
    "почти", "вышел", "вышла", "своего", "свои", "наш", "ваш", "их", "его",
    "могут", "может", "нужно", "надо", "будет", "есть", "нет",
    # EN fillers
    "the", "a", "an", "and", "or", "of", "in", "on", "to", "is", "are", "was", "were",
}

# ============================================================================
# PROMETHEUS METRICS
# ============================================================================

trend_events_processed_total = Counter(
    "trend_events_processed_total",
    "posts.indexed events processed by TrendDetectionWorker",
    ["status"],
)

trend_emerging_events_total = Counter(
    "trend_emerging_events_total",
    "Emerging trend events emitted",
    ["status"],
)

trend_worker_latency_seconds = Histogram(
    "trend_worker_latency_seconds",
    "Latency of processing single posts.indexed event in trend worker",
    ["outcome"],
)

trend_card_llm_requests_total = Counter(
    "trend_card_llm_requests_total",
    "LLM enrichment attempts for trend cards",
    ["outcome"],
)

trend_cluster_sample_posts = Histogram(
    "trend_cluster_sample_posts",
    "Number of sample posts stored per cluster card",
    buckets=(0, 1, 2, 3, 5, 8, 10, 15),
)

# Context7: Метрики для диагностики порогов детекции трендов
trend_detection_ratio_histogram = Histogram(
    "trend_detection_ratio",
    "Burst ratio (freq_short / expected_baseline) for trend detection",
    buckets=(0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 10.0, float("inf")),
)

trend_detection_coherence_histogram = Histogram(
    "trend_detection_coherence",
    "Coherence (similarity) for trend detection",
    buckets=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.55, 0.6, 0.7, 0.8, 0.9, 1.0),
)

trend_detection_source_diversity_histogram = Histogram(
    "trend_detection_source_diversity",
    "Source diversity (number of unique channels) for trend detection",
    buckets=(0, 1, 2, 3, 5, 10, 15, 20, 30, 50, 100),
)

trend_detection_threshold_reasons = Counter(
    "trend_detection_threshold_reasons",
    "Reasons why trends are not emitted (threshold checks)",
    ["reason"],  # reason: ratio_too_low|source_diversity_too_low|coherence_too_low|cooldown|all_passed
)

# Context7: Метрики для мониторинга новых компонентов валидации кластеризации
trend_clustering_rejected_total = Counter(
    "trend_clustering_rejected_total",
    "Posts rejected from clusters by validation components",
    ["reason"],  # reason: dynamic_threshold|topic_gate|coherence_agent|graph_validation|drift_detection
)

trend_clustering_coherence_score_histogram = Histogram(
    "trend_clustering_coherence_score",
    "Coherence score distribution for cluster validation",
    buckets=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0),
)

trend_clustering_cluster_size_histogram = Histogram(
    "trend_clustering_cluster_size",
    "Cluster size distribution when validation is performed",
    buckets=(0, 1, 2, 3, 5, 10, 15, 20, 30, 50, 100),
)

trend_clustering_llm_gate_latency_seconds = Histogram(
    "trend_clustering_llm_gate_latency_seconds",
    "Latency of LLM Topic Gate validation",
    buckets=(0.0, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0),
)


# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class PostSnapshot:
    post_id: str
    channel_id: str
    channel_title: Optional[str]
    tenant_id: Optional[str]
    posted_at: Optional[datetime]
    content: str
    keywords: List[str]
    topics: List[str]
    engagements: Dict[str, Optional[int]]
    entities: List[str] = field(default_factory=list)
    grouped_id: Optional[int] = None  # Context7: Для дедупликации альбомов


# ============================================================================
# TREND DETECTION WORKER
# ============================================================================

class TrendDetectionWorker:
    """
    Reactive trend detector:
    - consumes posts.indexed events,
    - updates Redis windows,
    - manages cluster metadata in Postgres/Qdrant,
    - emits trends.emerging events.
    """

    def __init__(
        self,
        redis_url: str,
        database_url: str,
        qdrant_url: str,
    ):
        self.redis_url = redis_url
        self.database_url = database_url
        self.qdrant_url = qdrant_url

        self.redis_client: Optional[RedisStreamsClient] = None
        self.event_consumer: Optional[EventConsumer] = None
        self.publisher: Optional[EventPublisher] = None
        self.qdrant_client: Optional[QdrantClient] = None
        self.embedding_service: Optional[EmbeddingService] = None
        self.db_pool: Optional[asyncpg.Pool] = None
        self.coherence_agent: Optional[TrendCoherenceAgent] = None
        self.graph_service = None
        self.drift_detector: Optional[DriftDetectorAgent] = None
        self.keyword_extractor: Optional[TrendKeywordExtractor] = None

        self.redis_schema = TrendRedisSchema()
        self.collection_name = os.getenv("TRENDS_HOT_COLLECTION", "trends_hot")

        self.freq_ratio_threshold = float(os.getenv("TREND_FREQ_RATIO_THRESHOLD", "3.0"))
        self.min_source_diversity = int(os.getenv("TREND_MIN_SOURCE_DIVERSITY", "3"))
        self.similarity_threshold = float(os.getenv("TREND_COHERENCE_THRESHOLD", "0.55"))
        self.emerging_cooldown_sec = int(os.getenv("TREND_EMERGING_COOLDOWN_SEC", "900"))
        self.card_window_seconds = int(
            os.getenv("TREND_CARD_WINDOW_SECONDS", str(TrendWindow.MID_1H.seconds))
        )
        self.card_llm_enabled = os.getenv("TREND_CARD_LLM_ENABLED", "true").lower() == "true"
        self.card_llm_model = os.getenv("TREND_CARD_LLM_MODEL", "GigaChat")
        self.card_llm_max_tokens = int(os.getenv("TREND_CARD_LLM_MAX_TOKENS", "400"))
        self.card_llm_refresh_minutes = int(os.getenv("TREND_CARD_REFRESH_MINUTES", "10"))
        self.cluster_sample_limit = int(os.getenv("TREND_CLUSTER_SAMPLE_LIMIT", "10"))
        self.card_refresh_tracker: Dict[str, float] = {}
        # Context7: Конфигурация для LLM Topic Gate
        self.topic_gate_enabled = os.getenv("TREND_TOPIC_GATE_ENABLED", "true").lower() == "true"
        self.topic_gate_threshold = float(os.getenv("TREND_TOPIC_GATE_THRESHOLD", "0.70"))
        self.topic_gate_cluster_size = int(os.getenv("TREND_TOPIC_GATE_CLUSTER_SIZE", "3"))
        # Context7: Конфигурация для Coherence Agent
        self.coherence_agent_enabled = os.getenv("TREND_COHERENCE_AGENT_ENABLED", "true").lower() == "true"
        self.coherence_agent_threshold = float(os.getenv("TREND_COHERENCE_AGENT_THRESHOLD", "0.65"))
        # Context7: Конфигурация для Graph и Drift Detector
        self.graph_validation_enabled = os.getenv("TREND_GRAPH_VALIDATION_ENABLED", "true").lower() == "true"
        self.drift_detection_enabled = os.getenv("TREND_DRIFT_DETECTION_ENABLED", "true").lower() == "true"
        self.drift_threshold = float(os.getenv("TREND_DRIFT_THRESHOLD", "0.05"))
        user_stopwords = {
            token.strip().lower()
            for token in os.getenv("TREND_STOPWORDS", "").split(",")
            if token.strip()
        }
        self.keyword_stopwords: Set[str] = DEFAULT_TREND_STOPWORDS | EXPANDED_STOPWORDS | user_stopwords

        logger.info(
            "TrendDetectionWorker initialized",
            redis_url=self.redis_url,
            qdrant_url=self.qdrant_url,
            freq_ratio_threshold=self.freq_ratio_threshold,
            min_source_diversity=self.min_source_diversity,
            similarity_threshold=self.similarity_threshold,
        )

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self):
        """Initialize dependencies and begin consumption loop."""
        start_ts = time.time()
        await self._initialize()
        trend_events_processed_total.labels(status="ready").inc()
        logger.info("TrendDetectionWorker initialization completed", took=time.time() - start_ts)
        await self.event_consumer.consume_forever("posts.indexed", self._handle_message)

    async def stop(self):
        """Graceful shutdown."""
        if self.event_consumer:
            self.event_consumer.running = False
        if self.embedding_service and hasattr(self.embedding_service, "close"):
            close_method = getattr(self.embedding_service, "close")
            if asyncio.iscoroutinefunction(close_method):
                await close_method()
            else:
                close_method()
        if self.redis_client:
            await self.redis_client.disconnect()
        if self.db_pool:
            await self.db_pool.close()
        logger.info("TrendDetectionWorker stopped")

    async def _initialize(self):
        """Prepare Redis, DB, AI and event bus clients."""
        self.redis_client = RedisStreamsClient(self.redis_url)
        await self.redis_client.connect()
        self.publisher = EventPublisher(self.redis_client)

        consumer_config = ConsumerConfig(
            group_name=os.getenv("TREND_CONSUMER_GROUP", "trend_workers"),
            consumer_name=os.getenv("TREND_CONSUMER_NAME", f"trend_worker_{uuid.uuid4().hex[:6]}"),
            batch_size=int(os.getenv("TREND_BATCH_SIZE", "64")),
            block_time=int(os.getenv("TREND_BLOCK_MS", "1000")),
            retry_delay=5,
            idle_timeout=120,
        )
        self.event_consumer = EventConsumer(self.redis_client, consumer_config)

        dsn = self._normalize_database_url(self.database_url)
        self.db_pool = await asyncpg.create_pool(
            dsn,
            min_size=2,
            max_size=int(os.getenv("TREND_DB_POOL_MAX", "10")),
            command_timeout=30,
        )
        logger.info("TrendDetectionWorker DB pool ready", dsn=dsn)

        self.qdrant_client = QdrantClient(self.qdrant_url)
        await self.qdrant_client.connect()

        ai_adapter = await create_gigachain_adapter()
        self.embedding_service = await create_embedding_service(ai_adapter)

        # Context7: Инициализация Coherence Agent
        if self.coherence_agent_enabled:
            self.coherence_agent = create_coherence_agent()
            logger.info("TrendCoherenceAgent initialized in TrendDetectionWorker")

        # Context7: Инициализация GraphService для Graph-RAG валидации
        graph_validation_enabled = os.getenv("TREND_GRAPH_VALIDATION_ENABLED", "true").lower() == "true"
        if graph_validation_enabled:
            try:
                self.graph_service = get_graph_service()
                await self.graph_service.connect()
                logger.info("GraphService initialized in TrendDetectionWorker")
            except Exception as exc:
                logger.warning("Failed to initialize GraphService", error=str(exc))
                self.graph_service = None

        # Context7: Инициализация Drift Detector
        if self.drift_detection_enabled and self.db_pool:
            self.drift_detector = DriftDetectorAgent(db_pool=self.db_pool)
            logger.info("DriftDetectorAgent initialized in TrendDetectionWorker")

        # Context7: Инициализация Keyword Extractor для c-TF-IDF
        keyword_extractor_enabled = os.getenv("TREND_CTFIDF_ENABLED", "true").lower() == "true"
        if keyword_extractor_enabled and self.db_pool:
            self.keyword_extractor = create_keyword_extractor(db_pool=self.db_pool)
            logger.info("TrendKeywordExtractor initialized in TrendDetectionWorker")

    # ------------------------------------------------------------------ #
    # Event processing
    # ------------------------------------------------------------------ #

    async def _handle_message(self, message: Dict[str, Any]):
        """Process single Redis message."""
        process_start = time.time()
        payload = self._extract_payload(message)
        post_id = payload.get("post_id")
        if not post_id:
            trend_events_processed_total.labels(status="invalid").inc()
            logger.warning(
                "trend_worker_invalid_event",
                error="post_id missing",
                payload_keys=list(payload.keys()),
            )
            return

        try:
            # Context7: Детальное логирование начала обработки
            logger.debug(
                "trend_worker_processing_event",
                post_id=post_id,
                tenant_id=payload.get("tenant_id"),
            )
            
            snapshot = await self._fetch_post_snapshot(post_id)
            if not snapshot:
                trend_events_processed_total.labels(status="missing_post").inc()
                logger.debug(
                    "trend_worker_post_not_found",
                    post_id=post_id,
                )
                return

            # Context7: Дедупликация альбомов - пропускаем посты из альбомов, если уже обработан другой пост из того же альбома
            # Для альбомов обрабатываем только пост с наивысшим engagement_score
            if snapshot.grouped_id:
                should_skip = await self._should_skip_album_post(snapshot)
                if should_skip:
                    trend_events_processed_total.labels(status="album_duplicate").inc()
                    logger.debug(
                        "trend_worker_album_duplicate_skipped",
                        post_id=post_id,
                        grouped_id=snapshot.grouped_id,
                    )
                    trend_worker_latency_seconds.labels(outcome="skipped_album").observe(time.time() - process_start)
                    return

            embedding = await self._generate_embedding(snapshot)
            cluster_id, cluster_key, similarity = await self._match_cluster(
                embedding, snapshot
            )
            coherence = similarity if similarity is not None else 0.0
            novelty = max(0.0, 1.0 - coherence) if similarity is not None else 1.0

            # Context7: Coherence Agent - валидация тематической когерентности
            if (
                self.coherence_agent_enabled
                and self.coherence_agent
                and cluster_id is not None
                and similarity is not None
                and similarity >= self.coherence_agent_threshold
            ):
                cluster_data = await self._get_cluster_data(cluster_id)
                if cluster_data:
                    coherence_validation = await self.coherence_agent.validate_post_cluster_match(
                        post_content=snapshot.content or "",
                        post_keywords=snapshot.keywords or [],
                        post_topics=snapshot.topics or [],
                        cluster_label=cluster_data.get("label") or cluster_data.get("primary_topic") or "",
                        cluster_summary=cluster_data.get("summary") or "",
                        cluster_keywords=cluster_data.get("keywords") or [],
                        cluster_topics=cluster_data.get("topics") or [],
                        similarity=similarity,
                    )
                    decision = coherence_validation.get("decision", "accept")
                    if decision == "reject":
                        logger.debug(
                            "trend_worker_coherence_agent_rejected",
                            cluster_id=cluster_id,
                            post_id=post_id,
                            similarity=similarity,
                            reasoning=coherence_validation.get("reasoning"),
                        )
                        trend_clustering_rejected_total.labels(reason="coherence_agent").inc()
                        # Создаем новый кластер вместо добавления в существующий
                        cluster_id = None
                        cluster_key = None
                        coherence = max(coherence, 0.6)
                    elif decision == "split":
                        logger.info(
                            "trend_worker_coherence_agent_split_suggestion",
                            cluster_id=cluster_id,
                            post_id=post_id,
                            similarity=similarity,
                            reasoning=coherence_validation.get("reasoning"),
                        )
                        # Пока не разделяем автоматически, только логируем
                        # В будущем можно добавить автоматическое разделение

            # Context7: Graph Validator - проверка связности тем через граф
            if (
                self.graph_validation_enabled
                and self.graph_service
                and cluster_id is not None
            ):
                try:
                    cluster_data = await self._get_cluster_data(cluster_id)
                    if cluster_data:
                        graph_validator = GraphClusterValidator(self.graph_service)
                        graph_check = await graph_validator.validate_cluster_topic(
                            post_topics=snapshot.topics or [],
                            post_entities=snapshot.entities or [],
                            cluster_id=cluster_id,
                            cluster_topics=cluster_data.get("topics") or [],
                        )
                        if graph_check.get("is_disconnected", False):
                            logger.debug(
                                "trend_worker_graph_validator_rejected",
                                cluster_id=cluster_id,
                                post_id=post_id,
                                post_topics=snapshot.topics,
                                cluster_topics=cluster_data.get("topics"),
                                reasoning=graph_check.get("reasoning"),
                            )
                            trend_clustering_rejected_total.labels(reason="graph_validation").inc()
                            # Создаем новый кластер вместо добавления в существующий
                            cluster_id = None
                            cluster_key = None
                            coherence = max(coherence, 0.6)
                except Exception as exc:
                    logger.debug(
                        "trend_worker_graph_validator_failed",
                        error=str(exc),
                        cluster_id=cluster_id,
                        post_id=post_id,
                    )
                    # При ошибке продолжаем обработку

            # Context7: Drift Detector - проверка дрейфа темы кластера
            if (
                self.drift_detection_enabled
                and self.drift_detector
                and cluster_id is not None
                and embedding is not None
            ):
                cluster_size = await self._get_cluster_size(cluster_id)
                # Проверяем дрейф только для кластеров с достаточным количеством постов
                if cluster_size >= 5:
                    try:
                        drift_result = await self.drift_detector.detect_drift(
                            cluster_id=cluster_id, new_post_embedding=embedding
                        )
                        if drift_result.get("drift_detected", False):
                            logger.info(
                                "trend_worker_drift_detected",
                                cluster_id=cluster_id,
                                post_id=post_id,
                                delta=drift_result.get("delta"),
                                reasoning=drift_result.get("reasoning"),
                            )
                            trend_clustering_rejected_total.labels(reason="drift_detection").inc()
                            # Пока только логируем, не блокируем добавление поста
                            # В будущем можно создать новый кластер при обнаружении дрейфа
                    except Exception as exc:
                        logger.debug(
                            "trend_worker_drift_detector_failed",
                            error=str(exc),
                            cluster_id=cluster_id,
                            post_id=post_id,
                        )
                        # При ошибке продолжаем обработку

            # Context7: Вычисляем primary_topic заранее для проверки дубликатов
            raw_topics = snapshot.topics or []
            raw_keywords = snapshot.keywords or []
            raw_entities = snapshot.entities or []
            filtered_topics = self._filter_terms(raw_topics)
            filtered_keywords = self._filter_terms(raw_keywords)
            filtered_entities = self._filter_terms(raw_entities)
            candidates = filtered_entities + filtered_topics + filtered_keywords
            if not candidates:
                candidates = self._filter_terms(self._extract_entities_from_content(snapshot.content))
            primary_topic = self._build_primary_label(
                entities=filtered_entities,
                topics=filtered_topics,
                keywords=filtered_keywords,
                content=snapshot.content,
            )
            
            if cluster_id is None or cluster_key is None:
                # Context7: Проверяем существующие кластеры с таким же primary_topic/label перед созданием нового
                existing_cluster = await self._find_cluster_by_label(primary_topic)
                if existing_cluster:
                    cluster_id = existing_cluster["id"]
                    cluster_key = existing_cluster["cluster_key"]
                    logger.info(
                        "trend_worker_reused_existing_cluster",
                        cluster_id=cluster_id,
                        label=primary_topic,
                        post_id=snapshot.post_id,
                    )
                else:
                    cluster_id = str(uuid.uuid4())
                    cluster_key = self._build_cluster_key(snapshot)
                coherence = max(coherence, 0.6)
            cluster_id = self._normalize_cluster_id(cluster_id)

            freq_short = await self._increment_window(cluster_key, TrendWindow.SHORT_5M)
            freq_long = await self._increment_window(cluster_key, TrendWindow.MID_1H)
            freq_baseline = await self._increment_window(cluster_key, TrendWindow.LONG_24H)
            source_diversity = await self._update_source_diversity(cluster_key, snapshot.channel_id)
            expected_short_baseline = self._expected_baseline(freq_baseline, TrendWindow.SHORT_5M.seconds)
            burst_detection = self._compute_burst(freq_short, expected_short_baseline)
            window_mentions = freq_long
            window_baseline = self._expected_baseline(freq_baseline, TrendWindow.MID_1H.seconds)
            burst_window = self._compute_burst(window_mentions, window_baseline)
            rate_of_change = freq_short - max(freq_long, 1)
            window_end = datetime.now(timezone.utc)
            window_start = window_end - timedelta(seconds=self.card_window_seconds)
            summary_text = (snapshot.content or "")[:400]
            is_generic = self._is_generic_label(primary_topic)
            # Context7: Определяем topics и keywords_for_card ДО вызова _estimate_quality
            secondary = [term for term in candidates if term != primary_topic]
            topics = (filtered_entities + filtered_topics)[:5] or secondary[:5]
            keywords_for_card = (filtered_keywords + filtered_topics + filtered_entities)[:10] or filtered_keywords or raw_keywords[:10]
            quality_score = self._estimate_quality(
                primary_topic=primary_topic,
                burst_score=burst_window,
                source_diversity=source_diversity,
                window_mentions=window_mentions,
                len_keywords=len(keywords_for_card),
                len_topics=len(topics),
            )
            why_important = self._build_why_important(
                window_mentions=window_mentions,
                window_baseline=window_baseline,
                window_start=window_start,
                window_end=window_end,
            )
            await self._record_cluster_sample(cluster_id, snapshot)
            sample_posts = await self._fetch_cluster_samples(
                cluster_id, limit=min(5, self.cluster_sample_limit)
            )
            if not sample_posts:
                sample_posts = [self._snapshot_to_example(snapshot)]
            
            # Context7: Вычисление c-TF-IDF для улучшения keywords кластера
            if self.keyword_extractor and cluster_id:
                try:
                    # Получаем keywords всех постов кластера
                    cluster_posts_keywords = await self._get_cluster_posts_keywords(cluster_id)
                    if cluster_posts_keywords:
                        # Добавляем keywords текущего поста
                        current_post_keywords = [snapshot.keywords] if snapshot.keywords else []
                        all_posts_keywords = cluster_posts_keywords + current_post_keywords
                        
                        # Вычисляем c-TF-IDF
                        ctfidf_keywords = await self.keyword_extractor.compute_ctfidf_keywords_simple(
                            cluster_keywords=keywords_for_card,
                            cluster_posts_keywords=all_posts_keywords,
                            all_clusters_keywords=None,  # Опционально: можно передать для лучшего IDF
                            top_n=10,
                        )
                        
                        # Обновляем keywords_for_card взвешенными keywords из c-TF-IDF
                        if ctfidf_keywords:
                            weighted_keywords = [kw for kw, _ in ctfidf_keywords]
                            # Объединяем: сначала c-TF-IDF keywords (приоритет), затем существующие
                            seen = {kw.lower().strip() for kw in weighted_keywords}
                            keywords_for_card = weighted_keywords + [
                                kw for kw in keywords_for_card
                                if kw.lower().strip() not in seen
                            ][:10]
                except Exception as exc:
                    logger.debug(
                        "trend_worker_ctfidf_failed",
                        error=str(exc),
                        cluster_id=cluster_id,
                    )
                    # При ошибке продолжаем с обычными keywords
            
            llm_card = await self._enhance_card_with_llm(
                cluster_id=cluster_id,
                primary_topic=primary_topic,
                summary=summary_text,
                keywords=keywords_for_card,
                topics=topics,
                window_minutes=max(1, int(self.card_window_seconds / 60)),
                window_mentions=window_mentions,
                window_baseline=window_baseline,
                sources=source_diversity,
                sample_posts=sample_posts,
            )
            if llm_card:
                primary_topic = llm_card.get("title") or primary_topic
                summary_text = llm_card.get("summary") or summary_text
                why_important = llm_card.get("why_important") or why_important
                llm_topics = llm_card.get("topics")
                if llm_topics:
                    topics = [topic for topic in llm_card.get("topics", []) if topic][:5]
            else:
                summary_text = self._summarize_samples(sample_posts) or summary_text
            card_payload = self._build_card_payload(
                cluster_key=cluster_key,
                title=primary_topic,
                summary=summary_text,
                keywords=keywords_for_card,
                topics=topics,
                window_start=window_start,
                window_end=window_end,
                window_mentions=window_mentions,
                window_baseline=window_baseline,
                burst_score=burst_window,
                sources=source_diversity,
                channels=source_diversity,
                coherence=coherence,
                why_important=why_important,
                sample_posts=sample_posts,
            )

            # Context7: Получаем существующий label кластера, чтобы не перезаписать хороший label плохим primary_topic
            existing_cluster_data = await self._get_cluster_data(cluster_id) if cluster_id else None
            existing_label = existing_cluster_data.get("label") if existing_cluster_data else None
            
            # Context7: Используем label для card_payload.title, если он лучше primary_topic
            # Если существует хороший label (не generic), используем его, иначе используем primary_topic из LLM или текущий
            card_title = existing_label if (existing_label and not self._is_generic_label(existing_label)) else primary_topic
            if llm_card and llm_card.get("title"):
                # LLM-заголовок имеет приоритет, если он не generic
                llm_title = llm_card.get("title")
                if not self._is_generic_label(llm_title):
                    card_title = llm_title
            
            # Обновляем card_payload с правильным title
            card_payload["title"] = card_title
            
            # Context7: Определяем label для сохранения в БД - приоритет LLM title, затем существующий label, затем primary_topic
            db_label = card_title  # Используем card_title как label для БД
            
            cluster_id = await self._upsert_cluster(
                cluster_id=cluster_id,
                cluster_key=cluster_key,
                snapshot=snapshot,
                embedding=embedding,
                coherence=coherence,
                novelty=novelty,
                source_diversity=source_diversity,
                primary_topic=primary_topic,
                label=db_label,  # Context7: Передаём label явно
                summary=summary_text,
                window_start=window_start,
                window_end=window_end,
                window_mentions=window_mentions,
                freq_baseline=freq_baseline,
                burst_window=burst_window,
                channels_count=source_diversity,
                why_important=why_important,
                topics=topics,
                card_payload=card_payload,
                is_generic=is_generic,
                quality_score=quality_score,
            )
            await self._upsert_metrics(
                cluster_id=cluster_id,
                freq_short=freq_short,
                freq_long=freq_long,
                freq_baseline=freq_baseline,
                rate_of_change=rate_of_change,
                burst_score=burst_detection,
                source_diversity=source_diversity,
                coherence=coherence,
            )

            # Context7: Метрики для диагностики порогов детекции
            ratio = self._compute_burst(freq_short, expected_short_baseline)
            trend_detection_ratio_histogram.observe(ratio)
            trend_detection_coherence_histogram.observe(coherence)
            trend_detection_source_diversity_histogram.observe(source_diversity)
            # Context7: Дополнительная метрика coherence для валидации кластеризации (если кластер найден)
            if cluster_id is not None:
                trend_clustering_coherence_score_histogram.observe(coherence)
            
            # Context7: Детальное логирование значений для диагностики
            logger.debug(
                "trend_worker_detection_values",
                post_id=post_id,
                cluster_key=cluster_key,
                ratio=ratio,
                coherence=coherence,
                source_diversity=source_diversity,
                freq_short=freq_short,
                expected_baseline=expected_short_baseline,
                freq_ratio_threshold=self.freq_ratio_threshold,
                coherence_threshold=self.similarity_threshold,
                min_source_diversity=self.min_source_diversity,
            )

            await self._maybe_emit_emerging(
                cluster_id=cluster_id,
                cluster_key=cluster_key,
                snapshot=snapshot,
                freq_short=freq_short,
                expected_baseline=expected_short_baseline,
                source_diversity=source_diversity,
                burst_score=burst_detection,
                coherence=coherence,
                primary_topic=primary_topic,
                keywords=keywords_for_card,
            )

            # Context7: Метрика успешной обработки
            trend_events_processed_total.labels(status="processed").inc()
            trend_worker_latency_seconds.labels(outcome="success").observe(time.time() - process_start)
            
            logger.debug(
                "trend_worker_event_processed",
                post_id=post_id,
                cluster_key=cluster_key,
                processing_time_ms=int((time.time() - process_start) * 1000),
            )

        except Exception as exc:
            trend_events_processed_total.labels(status="error").inc()
            trend_worker_latency_seconds.labels(outcome="error").observe(time.time() - process_start)
            logger.error(
                "trend_worker_processing_error",
                error=str(exc),
                post_id=post_id,
                exc_info=True,
            )
            raise

    # ------------------------------------------------------------------ #
    # Payload helpers
    # ------------------------------------------------------------------ #

    def _extract_payload(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize Redis message shape to dict."""
        if "payload" in message:
            payload = message["payload"]
        elif "data" in message:
            raw = message["data"]
            if isinstance(raw, (bytes, bytearray)):
                payload = json.loads(raw)
            elif isinstance(raw, str):
                payload = json.loads(raw)
            else:
                payload = raw
        else:
            payload = message
        if not isinstance(payload, dict):
            raise ValueError("Unsupported payload format")
        return payload

    def _snapshot_to_example(self, snapshot: PostSnapshot) -> Dict[str, Any]:
        return {
            "post_id": snapshot.post_id,
            "channel_id": snapshot.channel_id,
            "channel_title": snapshot.channel_title,
            "posted_at": snapshot.posted_at.isoformat() if snapshot.posted_at else None,
            "content_snippet": (snapshot.content or "")[:400],
        }

    async def _record_cluster_sample(self, cluster_id: str, snapshot: PostSnapshot):
        if not self.db_pool:
            return
        try:
            cluster_uuid = uuid.UUID(cluster_id)
            post_uuid = uuid.UUID(snapshot.post_id)
        except (ValueError, TypeError):
            return
        channel_uuid = None
        if snapshot.channel_id:
            try:
                channel_uuid = uuid.UUID(snapshot.channel_id)
            except (ValueError, TypeError):
                channel_uuid = None
        snippet = (snapshot.content or "").strip()
        # Context7: Используем fallback на channel_title или post_id, если snippet пустой
        # Это позволяет сохранять посты даже без текста (например, только медиа)
        if not snippet:
            snippet = snapshot.channel_title or f"Post {snapshot.post_id[:8]}"
        snippet = snippet[:600]
        posted_at = snapshot.posted_at
        insert_query = """
            INSERT INTO trend_cluster_posts (
                id,
                cluster_id,
                post_id,
                channel_id,
                channel_title,
                content_snippet,
                posted_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (cluster_id, post_id)
            DO UPDATE SET
                channel_title = COALESCE(EXCLUDED.channel_title, trend_cluster_posts.channel_title),
                content_snippet = EXCLUDED.content_snippet,
                posted_at = EXCLUDED.posted_at;
        """
        cleanup_query = """
            DELETE FROM trend_cluster_posts
            WHERE cluster_id = $1
              AND id IN (
                  SELECT id
                  FROM trend_cluster_posts
                  WHERE cluster_id = $1
                  ORDER BY COALESCE(posted_at, created_at) DESC, created_at DESC
                  OFFSET $2
              );
        """
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    insert_query,
                    uuid.uuid4(),
                    cluster_uuid,
                    post_uuid,
                    channel_uuid,
                    snapshot.channel_title,
                    snippet,
                    posted_at,
                )
                if self.cluster_sample_limit > 0:
                    await conn.execute(cleanup_query, cluster_uuid, self.cluster_sample_limit)
        except asyncpg.ForeignKeyViolationError:
            logger.debug("trend_sample_cluster_missing", cluster_id=cluster_id)
            return

    async def _fetch_cluster_samples(self, cluster_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        if not self.db_pool or limit <= 0:
            return []
        try:
            cluster_uuid = uuid.UUID(cluster_id)
        except (ValueError, TypeError):
            return []
        # Отдаём свежие примеры с приоритетом разнообразия источников:
        # DISTINCT ON (channel_id) с сортировкой по posted_at DESC
        query = """
            SELECT DISTINCT ON (channel_id)
                post_id::text AS post_id,
                channel_id::text AS channel_id,
                channel_title,
                content_snippet,
                posted_at
            FROM trend_cluster_posts
            WHERE cluster_id = $1
            ORDER BY channel_id, COALESCE(posted_at, created_at) DESC
            LIMIT $2;
        """
        async with self.db_pool.acquire() as conn:
            records = await conn.fetch(query, cluster_uuid, limit)
        samples: List[Dict[str, Any]] = []
        for record in records:
            samples.append(
                {
                    "post_id": record.get("post_id"),
                    "channel_id": record.get("channel_id"),
                    "channel_title": record.get("channel_title"),
                    "posted_at": record.get("posted_at").isoformat() if record.get("posted_at") else None,
                    "content_snippet": record.get("content_snippet"),
                }
            )
        return samples

    async def _get_cluster_size(self, cluster_id: str) -> int:
        """Получить количество постов в кластере."""
        if not self.db_pool:
            return 0
        try:
            cluster_uuid = uuid.UUID(cluster_id)
        except (ValueError, TypeError):
            return 0
        query = """
            SELECT COUNT(*) FROM trend_cluster_posts
            WHERE cluster_id = $1;
        """
        async with self.db_pool.acquire() as conn:
            count = await conn.fetchval(query, cluster_uuid)
        return count or 0

    async def _get_cluster_posts_keywords(self, cluster_id: str) -> List[List[str]]:
        """Получить keywords всех постов кластера для c-TF-IDF."""
        if not self.db_pool:
            return []
        try:
            cluster_uuid = uuid.UUID(cluster_id)
        except (ValueError, TypeError):
            return []
        
        query = """
            SELECT COALESCE(pe.data->'keywords', '[]'::jsonb) AS keywords
            FROM trend_cluster_posts tcp
            JOIN posts p ON p.id = tcp.post_id
            LEFT JOIN post_enrichment pe ON pe.post_id = p.id AND pe.kind = 'classify'
            WHERE tcp.cluster_id = $1
            AND pe.data->'keywords' IS NOT NULL
            LIMIT 100;  -- Ограничиваем для производительности
        """
        async with self.db_pool.acquire() as conn:
            records = await conn.fetch(query, cluster_uuid)
        
        all_keywords = []
        for record in records:
            keywords_raw = record.get("keywords")
            if isinstance(keywords_raw, list):
                all_keywords.append(keywords_raw)
            elif isinstance(keywords_raw, str):
                try:
                    keywords = json.loads(keywords_raw)
                    if isinstance(keywords, list):
                        all_keywords.append(keywords)
                except (json.JSONDecodeError, TypeError):
                    pass
        
        return all_keywords

    async def _get_cluster_label(self, cluster_id: str) -> Optional[str]:
        """Получить label (primary_topic или label) кластера."""
        if not self.db_pool:
            return None
        try:
            cluster_uuid = uuid.UUID(cluster_id)
        except (ValueError, TypeError):
            return None
        query = """
            SELECT COALESCE(label, primary_topic) as cluster_label
            FROM trend_clusters
            WHERE id = $1;
        """
        async with self.db_pool.acquire() as conn:
            label = await conn.fetchval(query, cluster_uuid)
        return label

    async def _get_cluster_data(self, cluster_id: str) -> Optional[Dict[str, Any]]:
        """Получить данные кластера (label, summary, keywords, topics, primary_topic)."""
        if not self.db_pool:
            return None
        try:
            cluster_uuid = uuid.UUID(cluster_id)
        except (ValueError, TypeError):
            return None
        query = """
            SELECT 
                label,
                summary,
                keywords,
                topics,
                primary_topic
            FROM trend_clusters
            WHERE id = $1;
        """
        async with self.db_pool.acquire() as conn:
            record = await conn.fetchrow(query, cluster_uuid)
        if not record:
            return None
        return {
            "label": record.get("label"),
            "summary": record.get("summary"),
            "keywords": record.get("keywords") or [],
            "topics": record.get("topics") or [],
            "primary_topic": record.get("primary_topic"),
        }

    async def _fetch_post_snapshot(self, post_id: str) -> Optional[PostSnapshot]:
        """Load post + enrichment details from Postgres."""
        if not self.db_pool:
            return None
        query = """
            SELECT
                p.id,
                p.channel_id,
                p.content,
                p.posted_at,
                p.views_count,
                p.reactions_count,
                p.forwards_count,
                p.replies_count,
                p.engagement_score,
                p.grouped_id,
                c.title AS channel_title,
                COALESCE(pe.data->'keywords', '[]'::jsonb) AS keywords,
                COALESCE(pe.data->'topics', '[]'::jsonb)   AS topics,
                COALESCE(pe.data->'metadata'->'topics', '[]'::jsonb) AS metadata_topics
            FROM posts p
            LEFT JOIN channels c ON c.id = p.channel_id
            LEFT JOIN post_enrichment pe
                ON pe.post_id = p.id AND pe.kind = 'classify'
            WHERE p.id = $1
            LIMIT 1;
        """
        try:
            post_uuid = uuid.UUID(str(post_id))
        except (ValueError, TypeError):
            logger.warning("trend_worker_invalid_post_id", post_id=post_id)
            return None

        async with self.db_pool.acquire() as conn:
            record = await conn.fetchrow(query, post_uuid)
        if not record:
            logger.debug("trend_worker_post_not_found", post_id=post_id)
            return None

        keywords = self._normalize_json_array(record.get("keywords"))
        topics = self._normalize_json_array(record.get("topics"))
        metadata_topics = self._normalize_json_array(record.get("metadata_topics"))
        combined_topics = (topics + metadata_topics)[:20]
        content = record.get("content") or ""
        if not keywords:
            keywords = self._fallback_keywords(content)
        entities = self._extract_entities_from_content(content)

        engagements = {
            "views": record.get("views_count"),
            "reactions": record.get("reactions_count"),
            "forwards": record.get("forwards_count"),
            "replies": record.get("replies_count"),
            "score": record.get("engagement_score"),
        }

        return PostSnapshot(
            post_id=str(record.get("id")),
            channel_id=str(record.get("channel_id")),
            channel_title=record.get("channel_title"),
            tenant_id=None,
            posted_at=record.get("posted_at"),
            content=content,
            keywords=keywords,
            topics=combined_topics,
            entities=entities,
            engagements=engagements,
            grouped_id=record.get("grouped_id"),  # Context7: Для дедупликации альбомов
        )

    async def _generate_embedding(self, snapshot: PostSnapshot) -> Optional[List[float]]:
        if not self.embedding_service:
            return None
        text_chunks = [
            snapshot.content.strip(),
            " ".join(snapshot.entities[:10]),
            " ".join(snapshot.keywords[:10]),
            " ".join(snapshot.topics[:10]),
        ]
        combined_text = " ".join(chunk for chunk in text_chunks if chunk).strip()
        if not combined_text:
            return None
        embedding = await self.embedding_service.generate_embedding_or_zeros(combined_text)
        return embedding

    async def _match_cluster(
        self,
        embedding: Optional[List[float]],
        snapshot: PostSnapshot,
    ) -> Tuple[Optional[str], Optional[str], Optional[float]]:
        if not embedding or not self.qdrant_client:
            return None, None, None
        try:
            results = await self.qdrant_client.search_vectors(
                collection_name=self.collection_name,
                query_vector=embedding,
                limit=3,
            )
        except Exception as exc:
            logger.debug("trend_worker_qdrant_search_failed", error=str(exc))
            return None, None, None

        if not results:
            return None, None, None

        top = results[0]
        similarity = float(top.get("score", 0.0))
        payload = top.get("payload", {}) or {}
        cluster_id = payload.get("cluster_id")
        cluster_key = payload.get("cluster_key")

        # Context7: Динамический порог когерентности в зависимости от размера кластера
        cluster_size = 0
        if cluster_id:
            cluster_size = await self._get_cluster_size(cluster_id)
            min_similarity = self._calculate_dynamic_threshold(cluster_size)
            # Метрики для мониторинга (только размер кластера, coherence измеряется в _handle_message)
            trend_clustering_cluster_size_histogram.observe(cluster_size)
        else:
            min_similarity = self.similarity_threshold

        if similarity < min_similarity or not cluster_id or not cluster_key:
            if cluster_id and similarity < min_similarity:
                trend_clustering_rejected_total.labels(reason="dynamic_threshold").inc()
            return None, None, similarity

        # Context7: LLM Topic Gate - дополнительная проверка тематической когерентности
        if (
            self.topic_gate_enabled
            and cluster_id
            and cluster_size >= self.topic_gate_cluster_size
            and similarity < self.topic_gate_threshold
        ):
            cluster_label = await self._get_cluster_label(cluster_id)
            if cluster_label:
                topic_gate_start = time.time()
                is_on_topic = await self._llm_topic_gate(
                    post_content=snapshot.content or "",
                    cluster_label=cluster_label,
                    similarity=similarity,
                )
                topic_gate_latency = time.time() - topic_gate_start
                trend_clustering_llm_gate_latency_seconds.observe(topic_gate_latency)
                if not is_on_topic:
                    logger.debug(
                        "trend_worker_topic_gate_rejected_post",
                        cluster_id=cluster_id,
                        cluster_label=cluster_label,
                        similarity=similarity,
                        post_id=snapshot.post_id,
                    )
                    trend_clustering_rejected_total.labels(reason="topic_gate").inc()
                    return None, None, similarity

        return cluster_id, cluster_key, similarity

    async def _find_cluster_by_label(self, label: str) -> Optional[Dict[str, Any]]:
        """
        Context7: Найти существующий кластер с таким же label/primary_topic.
        
        Best Practice: Проверяем дубликаты перед созданием нового кластера,
        чтобы избежать множественных кластеров с одинаковым label.
        Используем Redis кэш для оптимизации производительности (TTL 5 минут).
        """
        if not self.db_pool or not label:
            return None
        
        # Context7: Кэширование результатов в Redis для оптимизации
        cache_key = f"trend:cluster_by_label:{label[:50]}"  # Ограничиваем длину ключа
        cache_ttl = 300  # 5 минут
        
        try:
            # Проверяем кэш
            if self.redis_client and self.redis_client.client:
                cached = await self.redis_client.client.get(cache_key)
                if cached:
                    import json
                    result = json.loads(cached)
                    logger.debug(
                        "trend_worker_find_cluster_by_label_cache_hit",
                        label=label,
                        cluster_id=result.get("id"),
                    )
                    return result
        except Exception as exc:
            logger.debug("trend_worker_find_cluster_by_label_cache_error", error=str(exc), label=label)
        
        try:
            query = """
                SELECT id, cluster_key, label
                FROM trend_clusters
                WHERE status = 'emerging'
                  AND (label = $1 OR primary_topic = $1)
                  AND is_generic = false
                ORDER BY last_activity_at DESC
                LIMIT 1;
            """
            async with self.db_pool.acquire() as conn:
                record = await conn.fetchrow(query, label)
                if record:
                    result = {
                        "id": str(record.get("id")),
                        "cluster_key": record.get("cluster_key"),
                        "label": record.get("label"),
                    }
                    # Сохраняем в кэш
                    try:
                        if self.redis_client and self.redis_client.client:
                            import json
                            await self.redis_client.client.setex(
                                cache_key,
                                cache_ttl,
                                json.dumps(result),
                            )
                    except Exception as exc:
                        logger.debug("trend_worker_find_cluster_by_label_cache_set_error", error=str(exc), label=label)
                    return result
        except Exception as exc:
            logger.debug("trend_worker_find_cluster_by_label_failed", error=str(exc), label=label)
        return None

    def _calculate_dynamic_threshold(self, cluster_size: int) -> float:
        """
        Рассчитывает динамический порог когерентности в зависимости от размера кластера.
        
        Context7: Чем больше кластер, тем выше порог для предотвращения смешивания тем.
        """
        if cluster_size <= 2:
            return 0.55  # Базовый порог для новых кластеров
        elif cluster_size <= 5:
            return 0.65  # Повышенный порог для небольших кластеров
        elif cluster_size <= 10:
            return 0.70  # Высокий порог для средних кластеров
        else:
            return 0.75  # Очень высокий порог для больших кластеров

    def _build_cluster_key(self, snapshot: PostSnapshot) -> str:
        tokens = self._filter_terms(snapshot.entities + snapshot.topics + snapshot.keywords)
        if not tokens:
            tokens = self._filter_terms(self._extract_entities_from_content(snapshot.content))
        normalized = [t.lower().strip() for t in tokens if t]
        signature = "|".join(sorted(set(normalized)))
        if not signature:
            signature = snapshot.channel_id
        return hashlib.sha1(signature.encode("utf-8")).hexdigest()[:32]

    async def _upsert_cluster(
        self,
        cluster_id: str,
        cluster_key: str,
        snapshot: PostSnapshot,
        embedding: Optional[List[float]],
        coherence: float,
        novelty: float,
        source_diversity: int,
        primary_topic: str,
        summary: str,
        window_start: datetime,
        window_end: datetime,
        window_mentions: int,
        freq_baseline: int,
        burst_window: float,
        channels_count: int,
        why_important: Optional[str],
        topics: List[str],
        card_payload: Dict[str, Any],
        is_generic: bool,
        quality_score: float,
        label: Optional[str] = None,  # Context7: Явный label параметр
    ) -> str:
        if not self.db_pool:
            return cluster_id
        query = """
            INSERT INTO trend_clusters (
                id,
                cluster_key,
                status,
                label,
                summary,
                keywords,
                primary_topic,
                novelty_score,
                coherence_score,
                source_diversity,
                trend_embedding,
                first_detected_at,
                last_activity_at,
                window_start,
                window_end,
                window_mentions,
                freq_baseline,
                burst_score,
                sources_count,
                channels_count,
                why_important,
                topics,
                card_payload,
                is_generic,
                quality_score
            )
            VALUES (
                $1,
                $2,
                'emerging',
                $3,
                $4,
                $5::jsonb,
                $6,
                $7,
                $8,
                $9,
                $10,
                NOW(),
                NOW(),
                $11,
                $12,
                $13,
                $14,
                $15,
                $16,
                $17,
                $18,
                $19::jsonb,
                $20::jsonb,
                $21,
                $22
            )
            ON CONFLICT (cluster_key)
            DO UPDATE SET
                last_activity_at = NOW(),
                summary = COALESCE(EXCLUDED.summary, trend_clusters.summary),
                keywords = CASE
                    WHEN jsonb_array_length(EXCLUDED.keywords) > 0 THEN EXCLUDED.keywords
                    ELSE trend_clusters.keywords
                END,
                primary_topic = COALESCE(EXCLUDED.primary_topic, trend_clusters.primary_topic),
                label = CASE
                    -- Context7: Сохраняем существующий label, если он не generic и новый label хуже
                    WHEN trend_clusters.label IS NOT NULL 
                         AND LENGTH(trend_clusters.label) > 10 
                         AND trend_clusters.label NOT LIKE '%жизнь%' 
                         AND trend_clusters.label NOT LIKE '%летаю%'
                         AND (EXCLUDED.label IS NULL OR LENGTH(EXCLUDED.label) <= 10 OR EXCLUDED.label LIKE '%жизнь%' OR EXCLUDED.label LIKE '%летаю%')
                    THEN trend_clusters.label
                    -- Если передан явный label и он хороший, используем его
                    WHEN EXCLUDED.label IS NOT NULL AND LENGTH(EXCLUDED.label) > 4 
                         AND EXCLUDED.label NOT LIKE '%жизнь%' AND EXCLUDED.label NOT LIKE '%летаю%'
                    THEN EXCLUDED.label
                    -- Иначе пытаемся использовать primary_topic, если он хороший
                    WHEN EXCLUDED.primary_topic IS NOT NULL AND LENGTH(EXCLUDED.primary_topic) > 4 
                         AND EXCLUDED.primary_topic NOT LIKE '%жизнь%' AND EXCLUDED.primary_topic NOT LIKE '%летаю%'
                    THEN EXCLUDED.primary_topic
                    -- Fallback на существующий label или primary_topic
                    ELSE COALESCE(trend_clusters.label, EXCLUDED.label, EXCLUDED.primary_topic)
                END,
                novelty_score = COALESCE(EXCLUDED.novelty_score, trend_clusters.novelty_score),
                coherence_score = COALESCE(EXCLUDED.coherence_score, trend_clusters.coherence_score),
                source_diversity = GREATEST(trend_clusters.source_diversity, EXCLUDED.source_diversity),
                trend_embedding = COALESCE(EXCLUDED.trend_embedding, trend_clusters.trend_embedding),
                window_start = EXCLUDED.window_start,
                window_end = EXCLUDED.window_end,
                window_mentions = EXCLUDED.window_mentions,
                freq_baseline = EXCLUDED.freq_baseline,
                burst_score = EXCLUDED.burst_score,
                sources_count = EXCLUDED.sources_count,
                channels_count = EXCLUDED.channels_count,
                why_important = COALESCE(EXCLUDED.why_important, trend_clusters.why_important),
                topics = CASE
                    WHEN jsonb_array_length(EXCLUDED.topics) > 0 THEN EXCLUDED.topics
                    ELSE trend_clusters.topics
                END,
                card_payload = EXCLUDED.card_payload,
                is_generic = EXCLUDED.is_generic,
                quality_score = EXCLUDED.quality_score
            RETURNING id;
        """
        embedding_value = self._serialize_embedding(embedding) if embedding else None
        cluster_uuid = uuid.UUID(cluster_id)
        keywords_json = json.dumps(card_payload.get("keywords", [])) if card_payload.get("keywords") else "[]"
        topics_json = json.dumps(topics) if topics else "[]"
        card_payload_json = json.dumps(card_payload) if card_payload else "{}"
        # Context7: Используем label если передан, иначе primary_topic
        label_value = (label or primary_topic)[:255] if (label or primary_topic) else "Тренд"
        async with self.db_pool.acquire() as conn:
            db_cluster_id = await conn.fetchval(
                query,
                cluster_uuid,        # $1
                cluster_key,         # $2
                label_value,         # $3 (label для INSERT, используется как EXCLUDED.label в ON CONFLICT)
                summary,             # $4
                keywords_json,       # $5
                primary_topic[:255], # $6 (primary_topic)
                novelty,             # $7
                coherence,           # $8
                source_diversity,     # $9
                embedding_value,     # $10
                window_start,        # $11
                window_end,          # $12
                window_mentions,     # $13
                freq_baseline,       # $14
                burst_window,        # $15
                source_diversity,    # $16 (sources_count)
                channels_count,      # $17
                why_important,       # $18
                topics_json,         # $19
                card_payload_json,   # $20
                is_generic,          # $21
                quality_score,       # $22
            )
        cluster_id_str = str(db_cluster_id) if db_cluster_id else cluster_id

        if embedding and self.qdrant_client:
            payload = {
                "cluster_id": cluster_id_str,
                "cluster_key": cluster_key,
                "primary_topic": primary_topic,
                "channel_id": snapshot.channel_id,
            }
            try:
                await self.qdrant_client.upsert_vector(
                    collection_name=self.collection_name,
                    vector_id=cluster_key,
                    vector=embedding,
                    payload=payload,
                )
            except Exception as exc:
                logger.debug("trend_worker_qdrant_upsert_failed", error=str(exc), cluster_id=cluster_id_str)

        return cluster_id_str

    async def _upsert_metrics(
        self,
        cluster_id: str,
        freq_short: int,
        freq_long: int,
        freq_baseline: int,
        rate_of_change: float,
        burst_score: float,
        source_diversity: int,
        coherence: float,
    ):
        if not self.db_pool:
            return
        metrics_at = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        query = """
            INSERT INTO trend_metrics (
                id,
                cluster_id,
                freq_short,
                freq_long,
                freq_baseline,
                rate_of_change,
                burst_score,
                ewm_score,
                source_diversity,
                coherence_score,
                window_short_minutes,
                window_long_minutes,
                metrics_at
            )
            VALUES (
                $1,$2,$3,$4,$5,$6,$7,NULL,$8,$9,5,60,$10
            )
            ON CONFLICT (cluster_id, metrics_at)
            DO UPDATE SET
                freq_short = EXCLUDED.freq_short,
                freq_long = EXCLUDED.freq_long,
                freq_baseline = EXCLUDED.freq_baseline,
                rate_of_change = EXCLUDED.rate_of_change,
                burst_score = EXCLUDED.burst_score,
                source_diversity = EXCLUDED.source_diversity,
                coherence_score = EXCLUDED.coherence_score;
        """
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                query,
                uuid.uuid4(),
                uuid.UUID(cluster_id),
                freq_short,
                freq_long,
                freq_baseline,
                rate_of_change,
                burst_score,
                source_diversity,
                coherence,
                metrics_at,
            )

    async def _maybe_emit_emerging(
        self,
        cluster_id: str,
        cluster_key: str,
        snapshot: PostSnapshot,
        freq_short: int,
        expected_baseline: float,
        source_diversity: int,
        burst_score: float,
        coherence: float,
        primary_topic: str,
        keywords: List[str],
    ):
        ratio = self._compute_burst(freq_short, expected_baseline)
        
        # Context7: Детальная проверка порогов с логированием причин
        reasons = []
        if ratio < self.freq_ratio_threshold:
            reasons.append("ratio_too_low")
            trend_detection_threshold_reasons.labels(reason="ratio_too_low").inc()
        if source_diversity < self.min_source_diversity:
            reasons.append("source_diversity_too_low")
            trend_detection_threshold_reasons.labels(reason="source_diversity_too_low").inc()
        if coherence < self.similarity_threshold:
            reasons.append("coherence_too_low")
            trend_detection_threshold_reasons.labels(reason="coherence_too_low").inc()
        
        should_emit = (
            ratio >= self.freq_ratio_threshold
            and source_diversity >= self.min_source_diversity
            and coherence >= self.similarity_threshold
        )
        
        if not should_emit:
            logger.debug(
                "trend_worker_thresholds_not_met",
                cluster_key=cluster_key,
                ratio=ratio,
                ratio_threshold=self.freq_ratio_threshold,
                source_diversity=source_diversity,
                min_source_diversity=self.min_source_diversity,
                coherence=coherence,
                coherence_threshold=self.similarity_threshold,
                reasons=reasons,
            )
            return

        if await self._is_cluster_in_cooldown(cluster_key):
            trend_detection_threshold_reasons.labels(reason="cooldown").inc()
            logger.debug(
                "trend_worker_cooldown_active",
                cluster_key=cluster_key,
            )
            return
        
        # Context7: Все пороги пройдены
        trend_detection_threshold_reasons.labels(reason="all_passed").inc()

        event_payload = TrendEmergingEventV1(
            idempotency_key=f"trend:{cluster_id}:{snapshot.post_id}",
            cluster_id=cluster_id,
            cluster_key=cluster_key,
            post_id=snapshot.post_id,
            channel_id=snapshot.channel_id,
            channel_title=snapshot.channel_title,
            primary_topic=primary_topic,
            keywords=keywords[:10],
            freq_short=freq_short,
            freq_baseline=int(round(max(1.0, expected_baseline))),
            source_diversity=source_diversity,
            burst_score=burst_score,
            coherence=coherence,
            detected_at=datetime.now(timezone.utc),
        )
        try:
            await self.publisher.publish_event("trends.emerging", event_payload)
            trend_emerging_events_total.labels(status="published").inc()
        except Exception as exc:
            trend_emerging_events_total.labels(status="failed").inc()
            logger.error("trend_worker_emit_failed", error=str(exc), event=event_payload.model_dump())

    # ------------------------------------------------------------------ #
    # Redis helpers
    # ------------------------------------------------------------------ #

    async def _increment_window(self, cluster_key: str, window: TrendWindow) -> int:
        key = self.redis_schema.freq_key(cluster_key, window)
        redis = self.redis_client.client
        value = await redis.incr(key)
        await redis.expire(key, window.seconds)
        return value

    async def _should_skip_album_post(self, snapshot: PostSnapshot) -> bool:
        """
        Context7: Проверяет, нужно ли пропустить пост из альбома.
        Пропускаем, если уже обработан другой пост из того же альбома с более высоким engagement_score.
        """
        if not snapshot.grouped_id or not self.db_pool:
            return False
        
        try:
            # Получаем все посты из альбома с их engagement_score
            query = """
                SELECT id, engagement_score
                FROM posts
                WHERE grouped_id = $1
                ORDER BY COALESCE(engagement_score, 0) DESC, posted_at ASC
                LIMIT 10;
            """
            async with self.db_pool.acquire() as conn:
                records = await conn.fetch(query, snapshot.grouped_id)
            
            if not records or len(records) <= 1:
                return False
            
            # Находим пост с наивысшим engagement_score
            best_post_id = None
            best_engagement = -1
            for record in records:
                engagement = float(record.get("engagement_score") or 0)
                if engagement > best_engagement:
                    best_engagement = engagement
                    best_post_id = str(record.get("id"))
            
            # Если текущий пост не лучший, пропускаем его
            current_engagement = float(snapshot.engagements.get("score") or 0)
            if best_post_id and best_post_id != snapshot.post_id:
                # Проверяем, был ли лучший пост уже обработан (есть ли он в trend_cluster_posts)
                check_query = """
                    SELECT 1
                    FROM trend_cluster_posts
                    WHERE post_id = $1
                    LIMIT 1;
                """
                async with self.db_pool.acquire() as conn:
                    processed = await conn.fetchrow(check_query, uuid.UUID(best_post_id))
                
                if processed:
                    # Лучший пост уже обработан - пропускаем текущий
                    return True
                elif current_engagement < best_engagement:
                    # Текущий пост хуже лучшего, но лучший еще не обработан
                    # Пропускаем текущий, чтобы дать шанс лучшему
                    return True
            
            return False
        except Exception as e:
            logger.warning(
                "trend_worker_album_dedup_error",
                error=str(e),
                post_id=snapshot.post_id,
                grouped_id=snapshot.grouped_id,
            )
            # При ошибке не пропускаем пост
            return False

    async def _update_source_diversity(self, cluster_key: str, channel_id: str) -> int:
        redis = self.redis_client.client
        key = self.redis_schema.source_set_key(cluster_key)
        await redis.sadd(key, channel_id)
        await redis.expire(key, TrendWindow.LONG_24H.seconds)
        return await redis.scard(key)

    async def _is_cluster_in_cooldown(self, cluster_key: str) -> bool:
        redis = self.redis_client.client
        key = f"{self.redis_schema.namespace}:{cluster_key}:emitted"
        was_set = await redis.setnx(key, "1")
        if was_set:
            await redis.expire(key, self.emerging_cooldown_sec)
            return False
        return True

    # ------------------------------------------------------------------ #
    # Utility helpers
    # ------------------------------------------------------------------ #

    def _normalize_json_array(self, value: Any) -> List[str]:
        if not value:
            return []
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [str(v).strip() for v in parsed if str(v).strip()]
            except json.JSONDecodeError:
                return [value.strip()]
        return []

    def _fallback_keywords(self, content: str) -> List[str]:
        tokens = [
            token.lower()
            for token in content.split()
            if token.isalpha() and len(token) > 4
        ]
        unique = []
        for t in tokens:
            if t not in unique:
                unique.append(t)
            if len(unique) >= 10:
                break
        filtered = self._filter_terms(unique)
        return filtered[:10]

    def _serialize_embedding(self, embedding: List[float]) -> str:
        return "[" + ",".join(f"{float(v):.6f}" for v in embedding) + "]"

    def _expected_baseline(self, freq_baseline: int, window_seconds: int) -> float:
        long_window = TrendWindow.LONG_24H.seconds
        buckets = max(1, long_window // window_seconds)
        if freq_baseline <= 0:
            return 1.0
        return freq_baseline / buckets

    def _compute_burst(self, observed: int, expected: float) -> float:
        # Context7: Исправляем расчет burst score
        # Если expected < 1.0, это означает, что baseline очень низкий
        # В этом случае burst должен быть выше, поэтому используем минимальный baseline = 0.1
        if expected <= 0:
            return float(observed)  # Если baseline = 0, burst = observed
        baseline = max(0.1, expected)  # Минимальный baseline = 0.1, а не 1.0
        return round(observed / baseline, 2)

    def _build_why_important(
        self,
        window_mentions: int,
        window_baseline: float,
        window_start: datetime,
        window_end: datetime,
    ) -> str:
        ratio = self._compute_burst(window_mentions, window_baseline)
        duration_minutes = max(1, int((window_end - window_start).total_seconds() // 60))
        baseline = max(1, int(round(window_baseline)))
        return (
            f"За последние {duration_minutes} мин зафиксировано {window_mentions} упоминаний — "
            f"примерно в {ratio:.1f}× чаще, чем обычные {baseline} за период."
        )

    def _format_time_window(self, window_start: datetime, window_end: datetime) -> Dict[str, Any]:
        return {
            "from": window_start.isoformat(),
            "to": window_end.isoformat(),
            "duration_minutes": max(1, int((window_end - window_start).total_seconds() // 60)),
        }

    def _build_card_payload(
        self,
        cluster_key: str,
        title: str,
        summary: str,
        keywords: List[str],
        topics: List[str],
        window_start: datetime,
        window_end: datetime,
        window_mentions: int,
        window_baseline: float,
        burst_score: float,
        sources: int,
        channels: int,
        coherence: float,
        why_important: Optional[str],
        sample_posts: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        example_posts = []
        for post in sample_posts[:5]:
            example_posts.append(
                {
                    "post_id": post.get("post_id"),
                    "channel_id": post.get("channel_id"),
                    "channel_title": post.get("channel_title"),
                    "posted_at": post.get("posted_at"),
                    "content_snippet": post.get("content_snippet"),
                }
            )
        trend_cluster_sample_posts.observe(len(example_posts))
        payload = {
            "id": cluster_key,
            "title": title,
            "status": "emerging",
            "time_window": self._format_time_window(window_start, window_end),
            "stats": {
                "mentions": window_mentions,
                "baseline": max(1, int(round(window_baseline))),
                "burst_score": burst_score,
                "sources": sources,
                "channels": channels,
                "coherence": coherence,
            },
            "summary": summary,
            "why_important": why_important,
            "keywords": keywords,
            "topics": topics,
            "example_posts": example_posts,
        }
        return payload

    async def _enhance_card_with_llm(
        self,
        cluster_id: str,
        primary_topic: str,
        summary: str,
        keywords: List[str],
        topics: List[str],
        window_minutes: int,
        window_mentions: int,
        window_baseline: float,
        sources: int,
        sample_posts: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if not self.card_llm_enabled:
            return None
        if len(sample_posts) < 2 and window_mentions < 3:
            trend_card_llm_requests_total.labels(outcome="skipped").inc()
            return None
        now_ts = time.time()
        last_ts = self.card_refresh_tracker.get(cluster_id)
        if last_ts and now_ts - last_ts < self.card_llm_refresh_minutes * 60:
            trend_card_llm_requests_total.labels(outcome="skipped").inc()
            return None
        api_base = (
            getattr(settings, "openai_api_base", None)
            or os.getenv("OPENAI_API_BASE")
            or os.getenv("GIGACHAT_PROXY_URL")
            or "http://gpt2giga-proxy:8090"
        )
        api_base = api_base.rstrip("/")
        if not api_base.endswith("/v1"):
            api_base = f"{api_base}/v1"

        credentials = os.getenv("GIGACHAT_CREDENTIALS")
        scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
        api_key = (
            getattr(settings, "openai_api_key", None)
            or os.getenv("OPENAI_API_KEY")
            or ""
        )
        if credentials:
            auth_header = f"Bearer giga-cred-{credentials}:{scope}"
        elif api_key:
            auth_header = f"Bearer {api_key}"
        else:
            auth_header = None

        sample_descriptions = [
            {
                "source": post.get("channel_title") or "Источник",
                "snippet": post.get("content_snippet"),
                "posted_at": post.get("posted_at"),
            }
            for post in sample_posts[:5]
            if post.get("content_snippet")
        ]
        prompt_payload = {
            "primary_topic": primary_topic,
            "keywords": keywords,
            "topics": topics,
            "window_minutes": window_minutes,
            "mentions": window_mentions,
            "baseline": max(1, int(round(window_baseline))),
            "sources": sources,
            "sample_posts": sample_descriptions,
        }
        system_message = (
            "Ты — редактор трендов. Получаешь статистику по новости и формируешь краткую карточку. "
            "Верни JSON с полями title, summary, why_important, topics (список до 5 кратких тегов). "
            "Пиши по-русски, без Markdown."
        )
        user_message = (
            "Данные тренда:\n"
            f"{json.dumps(prompt_payload, ensure_ascii=False)}\n\n"
            "Ответь строго JSON объектом."
        )
        try:
            headers = {"Content-Type": "application/json"}
            if auth_header:
                headers["Authorization"] = auth_header
            endpoint_base = api_base.rstrip("/")
            if endpoint_base.endswith("/chat/completions"):
                endpoint = endpoint_base
            elif endpoint_base.endswith("/v1"):
                endpoint = f"{endpoint_base}/chat/completions"
            else:
                endpoint = f"{endpoint_base}/v1/chat/completions"
            trend_card_llm_requests_total.labels(outcome="requested").inc()
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.post(
                    endpoint,
                    headers=headers,
                    json={
                        "model": self.card_llm_model,
                        "messages": [
                            {"role": "system", "content": system_message},
                            {"role": "user", "content": user_message},
                        ],
                        "temperature": 0.2,
                        "max_tokens": self.card_llm_max_tokens,
                    },
                )
            if response.status_code != 200:
                logger.debug(
                    "trend_worker_llm_response_error",
                    status=response.status_code,
                    body=response.text[:200],
                )
                trend_card_llm_requests_total.labels(outcome="error").inc()
                return None
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            parsed = self._safe_parse_json_obj(content)
            if not parsed:
                trend_card_llm_requests_total.labels(outcome="error").inc()
                return None
            parsed_topics = parsed.get("topics")
            if isinstance(parsed_topics, list):
                parsed["topics"] = [str(topic).strip() for topic in parsed_topics if str(topic).strip()]
            else:
                parsed["topics"] = []
            self.card_refresh_tracker[cluster_id] = now_ts
            trend_card_llm_requests_total.labels(outcome="success").inc()
            return parsed
        except Exception as exc:
            trend_card_llm_requests_total.labels(outcome="error").inc()
            logger.debug("trend_worker_llm_failure", error=str(exc))
            return None

    async def _llm_topic_gate(
        self, post_content: str, cluster_label: str, similarity: float
    ) -> bool:
        """
        LLM Topic Gate: проверяет, принадлежит ли пост теме кластера.
        
        Context7: Используется для валидации тематической когерентности
        когда similarity ниже порога, но кластер достаточно большой.
        
        Returns:
            True если пост принадлежит теме кластера, False в противном случае
        """
        if not self.topic_gate_enabled:
            return True  # Если отключен, пропускаем проверку
        
        api_base = (
            getattr(settings, "openai_api_base", None)
            or os.getenv("OPENAI_API_BASE")
            or os.getenv("GIGACHAT_PROXY_URL")
            or "http://gpt2giga-proxy:8090"
        )
        api_base = api_base.rstrip("/")
        if not api_base.endswith("/v1"):
            api_base = f"{api_base}/v1"

        credentials = os.getenv("GIGACHAT_CREDENTIALS")
        scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
        api_key = (
            getattr(settings, "openai_api_key", None)
            or os.getenv("OPENAI_API_KEY")
            or ""
        )
        if credentials:
            auth_header = f"Bearer giga-cred-{credentials}:{scope}"
        elif api_key:
            auth_header = f"Bearer {api_key}"
        else:
            auth_header = None

        system_message = (
            "Ты — валидатор тематической когерентности. "
            "Проверяешь, принадлежит ли пост теме кластера. "
            "Ответь только одним словом: yes, no или borderline."
        )
        user_message = (
            f"Пост: {post_content[:500]}\n"
            f"Тема кластера: {cluster_label}\n"
            f"Семантическое сходство: {similarity:.2f}\n\n"
            f"Принадлежит ли этот пост теме кластера? "
            f"Ответь только: yes/no/borderline"
        )

        try:
            headers = {"Content-Type": "application/json"}
            if auth_header:
                headers["Authorization"] = auth_header
            endpoint_base = api_base.rstrip("/")
            if endpoint_base.endswith("/chat/completions"):
                endpoint = endpoint_base
            elif endpoint_base.endswith("/v1"):
                endpoint = f"{endpoint_base}/chat/completions"
            else:
                endpoint = f"{endpoint_base}/v1/chat/completions"
            
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                response = await client.post(
                    endpoint,
                    headers=headers,
                    json={
                        "model": self.card_llm_model,
                        "messages": [
                            {"role": "system", "content": system_message},
                            {"role": "user", "content": user_message},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 10,
                    },
                )
            if response.status_code != 200:
                logger.debug(
                    "trend_worker_topic_gate_llm_error",
                    status=response.status_code,
                    body=response.text[:200],
                )
                return True  # При ошибке пропускаем проверку
            data = response.json()
            content = data["choices"][0]["message"]["content"].strip().lower()
            
            # Парсинг ответа: yes -> True, no -> False, borderline -> True (но с логированием)
            if "yes" in content or "да" in content:
                return True
            elif "no" in content or "нет" in content:
                logger.debug(
                    "trend_worker_topic_gate_rejected",
                    cluster_label=cluster_label,
                    similarity=similarity,
                )
                return False
            else:
                # borderline или неопределенный ответ - пропускаем с логированием
                logger.debug(
                    "trend_worker_topic_gate_borderline",
                    cluster_label=cluster_label,
                    similarity=similarity,
                    response=content,
                )
                return True
        except Exception as exc:
            logger.debug("trend_worker_topic_gate_failure", error=str(exc))
            return True  # При ошибке пропускаем проверку

    def _normalize_database_url(self, url: str) -> str:
        if "+asyncpg" in url:
            return url.replace("+asyncpg", "")
        return url

    def _is_uuid(self, value: str) -> bool:
        try:
            uuid.UUID(str(value))
            return True
        except (ValueError, TypeError):
            return False

    def _normalize_cluster_id(self, cluster_id: Optional[str]) -> str:
        if cluster_id and self._is_uuid(cluster_id):
            return str(cluster_id)
        if cluster_id:
            deterministic = uuid.uuid5(uuid.NAMESPACE_DNS, cluster_id)
            return str(deterministic)
        return str(uuid.uuid4())

    def _safe_parse_json_obj(self, content: str) -> Optional[Dict[str, Any]]:
        if not content:
            return None
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned.replace("json", "", 1).strip()
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        return None

    def _filter_terms(self, terms: List[str]) -> List[str]:
        filtered: List[str] = []
        seen: Set[str] = set()
        for term in terms:
            if not term:
                continue
            normalized = term.strip()
            if not normalized:
                continue
            lower = normalized.lower()
            if lower in seen:
                continue
            if len(lower) < 3:
                continue
            if lower in self.keyword_stopwords:
                continue
            if lower.isdigit():
                continue
            if lower.startswith("@"):
                continue
            # чистим мусорные символы по краям
            normalized = normalized.strip(".,!?:;\"'()[]{}")
            if not normalized:
                continue
            # отсекаем одиночные токены без смысла (например, «первый», «почти»)
            if " " not in normalized and (len(normalized) < 4 or lower in self.keyword_stopwords):
                continue
            seen.add(lower)
            filtered.append(normalized)
        return filtered

    def _summarize_samples(self, sample_posts: List[Dict[str, Any]]) -> str:
        snippets: List[str] = []
        for post in sample_posts:
            snippet = (post.get("content_snippet") or "").strip()
            if not snippet:
                continue
            sanitized = snippet.replace("\n", " ")
            if sanitized and sanitized not in snippets:
                snippets.append(sanitized)
            if len(snippets) >= 2:
                break
        return " ".join(snippets)[:600]

    def _extract_entities_from_content(self, content: str) -> List[str]:
        if not content:
            return []
        entities: List[str] = []
        hashtags = re.findall(r"#([\w\d_]+)", content)
        for tag in hashtags:
            token = f"#{tag}"
            if token not in entities:
                entities.append(token)
        proper_pattern = re.compile(r"(?:[A-ZА-ЯЁ][\w\-]+(?:\s+[A-ZА-ЯЁ][\w\-]+){0,2})")
        for match in proper_pattern.findall(content):
            cleaned = match.strip()
            if len(cleaned) < 3:
                continue
            if cleaned not in entities:
                entities.append(cleaned)
        return entities

    def _extract_keyphrases_from_content(self, content: str) -> List[str]:
        """
        Простой keyphrase extractor без внешних библиотек:
        - выбираем биграммы/триграммы из слов длиной >=4, не стоп-слова
        - возвращаем топ уникальных по порядку появления
        """
        if not content:
            return []
        tokens = re.findall(r"[A-Za-zА-Яа-яЁё0-9#@][\w\-]+", content)
        tokens = [t for t in tokens if len(t) >= 4 and t.lower() not in self.keyword_stopwords and not t.isdigit()]
        phrases: List[str] = []
        # биграммы
        for i in range(len(tokens) - 1):
            a, b = tokens[i], tokens[i + 1]
            phrase = f"{a} {b}"
            phrases.append(phrase)
        # триграммы (реже, для более выразительных кейфраз)
        for i in range(len(tokens) - 2):
            a, b, c = tokens[i], tokens[i + 1], tokens[i + 2]
            phrase = f"{a} {b} {c}"
            phrases.append(phrase)
        filtered = self._filter_terms(phrases)
        unique: List[str] = []
        seen: Set[str] = set()
        for p in filtered:
            low = p.lower()
            if low in seen:
                continue
            seen.add(low)
            unique.append(p)
            if len(unique) >= 10:
                break
        return unique

    def _is_generic_label(self, label: str) -> bool:
        if not label:
            return True
        lower = label.lower().strip()
        if lower in self.keyword_stopwords:
            return True
        if len(lower) < 4:
            return True
        # одиночное слово без хэштега/сущности
        if " " not in lower and not lower.startswith("#"):
            return True
        return False

    def _estimate_quality(
        self,
        primary_topic: str,
        burst_score: float,
        source_diversity: int,
        window_mentions: int,
        len_keywords: int,
        len_topics: int,
    ) -> float:
        """Оценка качества кластера (0.0-1.0)."""
        score = 0.0
        # Базовые метрики
        if burst_score >= 3.0:
            score += 0.3
        elif burst_score >= 1.5:
            score += 0.15
        if source_diversity >= 5:
            score += 0.2
        elif source_diversity >= 3:
            score += 0.1
        if window_mentions >= 10:
            score += 0.2
        elif window_mentions >= 5:
            score += 0.1
        # Качество контента
        if len_keywords >= 5:
            score += 0.1
        if len_topics >= 3:
            score += 0.1
        # Штраф за generic title
        if self._is_generic_label(primary_topic):
            score *= 0.3  # Сильный штраф
        return min(1.0, score)

    def _build_primary_label(
        self,
        entities: List[str],
        topics: List[str],
        keywords: List[str],
        content: str,
    ) -> str:
        # 1) многословные сущности/хэштеги
        for token in entities:
            if not self._is_generic_label(token) and (" " in token or token.startswith("#")):
                return token[:120]
        # 2) темы (обычно короткие метки), соберём составную
        candidates = [t for t in topics if not self._is_generic_label(t)]
        if len(candidates) >= 2:
            return f"{candidates[0]} — {candidates[1]}"[:120]
        if len(candidates) == 1 and " " in candidates[0]:
            return candidates[0][:120]
        # 3) кейфразы из контента
        keyphrases = self._extract_keyphrases_from_content(content)
        for kp in keyphrases:
            if not self._is_generic_label(kp):
                return kp[:120]
        # 4) склеить 2–3 релевантных ключевых слова
        kw = [k for k in keywords if not self._is_generic_label(k)]
        if len(kw) >= 2:
            return f"{kw[0]} — {kw[1]}"[:120]
        if kw:
            return kw[0][:120]
        # 5) без вариантов — «Тренд»
        return "Тренд"


# ============================================================================
# FACTORY
# ============================================================================

async def create_trend_detection_worker() -> TrendDetectionWorker:
    """Factory for run_all_tasks integration."""
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    database_url = os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@supabase-db:5432/postgres"
    )
    qdrant_url = os.getenv("QDRANT_URL", "http://qdrant:6333")
    worker = TrendDetectionWorker(redis_url, database_url, qdrant_url)
    return worker


