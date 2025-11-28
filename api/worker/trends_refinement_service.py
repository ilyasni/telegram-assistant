"""
Trend Refinement Service для периодического улучшения качества кластеров.

Context7: Фоновый сервис, запускаемый периодически (каждые N часов):
- Оценивает метрики когерентности для всех активных кластеров
- Выявляет кластеры с низкой когерентностью (кандидаты на split)
- Находит похожие/мелкие кластеры (кандидаты на merge)
- Выполняет разделение/слияние асинхронно
- Создаёт подтемы для крупных кластеров (двухуровневая кластеризация)
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import asyncpg
import numpy as np
import structlog
from prometheus_client import Counter, Histogram, REGISTRY

from integrations.qdrant_client import QdrantClient

# Импорты с отложенной загрузкой для избежания проблем с зависимостями
try:
    from ai_providers.embedding_service import create_embedding_service, EmbeddingService
except ImportError:
    create_embedding_service = None
    EmbeddingService = None

try:
    from ai_providers.gigachain_adapter import create_gigachain_adapter
except ImportError:
    create_gigachain_adapter = None

try:
    from api.worker.trends_coherence_metrics import create_coherence_metrics, TrendCoherenceMetrics
except ImportError:
    # Fallback для worker контейнера
    try:
        from trends_coherence_metrics import create_coherence_metrics, TrendCoherenceMetrics
    except ImportError:
        create_coherence_metrics = None
        TrendCoherenceMetrics = None

try:
    from api.worker.trends_split_agent import create_split_agent, TrendSplitAgent
except ImportError:
    try:
        from trends_split_agent import create_split_agent, TrendSplitAgent
    except ImportError:
        create_split_agent = None
        TrendSplitAgent = None

try:
    from api.worker.trends_merge_agent import create_merge_agent, TrendMergeAgent
except ImportError:
    try:
        from trends_merge_agent import create_merge_agent, TrendMergeAgent
    except ImportError:
        create_merge_agent = None
        TrendMergeAgent = None

try:
    from api.worker.trends_keyword_extractor import create_keyword_extractor, TrendKeywordExtractor
except ImportError:
    try:
        from trends_keyword_extractor import create_keyword_extractor, TrendKeywordExtractor
    except ImportError:
        create_keyword_extractor = None
        TrendKeywordExtractor = None

logger = structlog.get_logger()


# ============================================================================
# PROMETHEUS METRICS
# ============================================================================

# Context7: Безопасная регистрация метрик (защита от дублирования)
def _safe_register_metric(metric_class, name, *args, **kwargs):
    """Безопасная регистрация метрики с обработкой дублирования."""
    try:
        return metric_class(name, *args, **kwargs)
    except ValueError as e:
        if 'Duplicated timeseries' in str(e) or 'already registered' in str(e).lower():
            # Метрика уже зарегистрирована, пытаемся найти её в registry
            for collector in list(REGISTRY._collector_to_names.keys()):
                if collector is not None and hasattr(collector, '_name') and collector._name == name:
                    logger.debug(f"{name} already registered, reusing existing", metric=name)
                    return collector
            # Если не нашли, создаём с уникальным именем как fallback
            logger.warning(f"{name} already registered but not found, creating with _v2 suffix", metric=name)
            return metric_class(f"{name}_v2", *args, **kwargs)
        else:
            raise

trend_refinement_runs_total = _safe_register_metric(
    Counter,
    "trend_refinement_runs_total",
    "Total number of refinement runs",
    ["status"],  # status: success|error
)

trend_refinement_clusters_split_total = _safe_register_metric(
    Counter,
    "trend_refinement_clusters_split_total",
    "Total number of clusters split during refinement",
)

trend_refinement_clusters_merged_total = _safe_register_metric(
    Counter,
    "trend_refinement_clusters_merged_total",
    "Total number of clusters merged during refinement",
)

trend_refinement_duration_seconds = _safe_register_metric(
    Histogram,
    "trend_refinement_duration_seconds",
    "Duration of refinement run in seconds",
    buckets=(10.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0),
)

trend_refinement_clusters_processed = _safe_register_metric(
    Histogram,
    "trend_refinement_clusters_processed",
    "Number of clusters processed per refinement run",
    buckets=(0, 10, 25, 50, 100, 250, 500, 1000),
)


# ============================================================================
# TREND REFINEMENT SERVICE
# ============================================================================


class TrendRefinementService:
    """
    Сервис рефайнмента кластеров трендов.
    
    Пайплайн:
    1. Оценить метрики когерентности для всех активных кластеров
    2. Найти кандидаты на split (низкая когерентность)
    3. Найти кандидаты на merge (похожие/мелкие кластеры)
    4. Выполнить split/merge с валидацией
    5. Создать подтемы для крупных кластеров (опционально)
    6. Обновить метрики в БД
    """

    def __init__(
        self,
        database_url: str,
        qdrant_url: str,
    ):
        """
        Инициализация TrendRefinementService.
        
        Args:
            database_url: URL подключения к БД
            qdrant_url: URL подключения к Qdrant
        """
        self.database_url = database_url
        self.qdrant_url = qdrant_url

        self.db_pool: Optional[asyncpg.Pool] = None
        self.qdrant_client: Optional[QdrantClient] = None
        self.embedding_service: Optional[EmbeddingService] = None
        self.coherence_metrics: Optional[TrendCoherenceMetrics] = None
        self.split_agent: Optional[TrendSplitAgent] = None
        self.merge_agent: Optional[TrendMergeAgent] = None
        self.keyword_extractor: Optional[TrendKeywordExtractor] = None

        # Конфигурация
        self.refinement_enabled = os.getenv("TREND_REFINEMENT_ENABLED", "true").lower() == "true"
        self.refinement_interval_hours = int(os.getenv("TREND_REFINEMENT_INTERVAL_HOURS", "6"))
        self.min_cluster_size_for_refinement = int(os.getenv("TREND_REFINEMENT_MIN_CLUSTER_SIZE", "3"))
        self.max_clusters_per_run = int(os.getenv("TREND_REFINEMENT_MAX_CLUSTERS_PER_RUN", "50"))
        self.subclustering_enabled = os.getenv("TREND_SUBCLUSTERING_ENABLED", "true").lower() == "true"
        self.subcluster_min_size = int(os.getenv("TREND_SUBCLUSTER_MIN_SIZE", "10"))

    async def initialize(self):
        """Инициализация зависимостей."""
        if not self.refinement_enabled:
            logger.info("Trend refinement service disabled")
            return

        # Инициализация БД
        # Context7: asyncpg требует postgresql:// (без +asyncpg)
        dsn = self.database_url
        if dsn.startswith("postgresql+asyncpg://"):
            dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
        elif not dsn.startswith("postgresql://"):
            # Если URL не начинается с postgresql://, добавляем префикс
            if "://" in dsn:
                # Извлекаем схему и заменяем на postgresql://
                parts = dsn.split("://", 1)
                dsn = f"postgresql://{parts[1]}"
            else:
                raise ValueError(f"Invalid database URL format: {dsn}")

        self.db_pool = await asyncpg.create_pool(
            dsn,
            min_size=2,
            max_size=10,
            command_timeout=60,
        )
        logger.info("TrendRefinementService DB pool ready")

        # Инициализация Qdrant
        self.qdrant_client = QdrantClient(self.qdrant_url)
        await self.qdrant_client.connect()
        logger.info("TrendRefinementService Qdrant client ready")

        # Инициализация Embedding Service (опционально, если доступен)
        if create_embedding_service and create_gigachain_adapter:
            try:
                ai_adapter = await create_gigachain_adapter()
                self.embedding_service = await create_embedding_service(ai_adapter)
                logger.info("TrendRefinementService Embedding service ready")
            except Exception as exc:
                logger.warning("Failed to initialize embedding service", error=str(exc))
                self.embedding_service = None
        else:
            logger.warning("Embedding service not available, skipping initialization")
            self.embedding_service = None

        # Инициализация компонентов
        self.coherence_metrics = create_coherence_metrics(db_pool=self.db_pool)
        self.keyword_extractor = create_keyword_extractor(db_pool=self.db_pool)
        self.split_agent = create_split_agent(
            db_pool=self.db_pool,
            qdrant_client=self.qdrant_client,
        )
        self.merge_agent = create_merge_agent(
            db_pool=self.db_pool,
            qdrant_client=self.qdrant_client,
            keyword_extractor=self.keyword_extractor,
        )
        logger.info("TrendRefinementService initialized")

    async def refine_clusters(self) -> Dict[str, Any]:
        """
        Выполнить рефайнмент кластеров: оценка метрик, split, merge.
        
        Returns:
            Dict с результатами рефайнмента:
            - clusters_processed: int
            - clusters_split: int
            - clusters_merged: int
            - subclusters_created: int
            - errors: List[str]
        """
        start_time = time.time()
        result = {
            "clusters_processed": 0,
            "clusters_split": 0,
            "clusters_merged": 0,
            "subclusters_created": 0,
            "errors": [],
        }

        if not self.refinement_enabled or not self.db_pool:
            logger.debug("Refinement disabled or DB pool unavailable")
            return result

        try:
            # Получаем активные кластеры для обработки
            clusters = await self._get_clusters_for_refinement()
            result["clusters_processed"] = len(clusters)

            if not clusters:
                logger.info("No clusters to refine")
                return result

            logger.info(
                "Starting cluster refinement",
                clusters_count=len(clusters),
            )

            # Шаг 1: Оцениваем метрики когерентности для всех кластеров
            for cluster in clusters[:self.max_clusters_per_run]:
                try:
                    await self._evaluate_cluster_metrics(cluster["id"])
                except Exception as exc:
                    error_msg = f"Error evaluating metrics for cluster {cluster['id']}: {str(exc)}"
                    logger.debug("refinement_metrics_error", error=error_msg, cluster_id=cluster["id"])
                    result["errors"].append(error_msg)

            # Шаг 2: Находим кандидаты на split
            split_candidates = []
            for cluster in clusters[:self.max_clusters_per_run]:
                try:
                    should_split_result = await self.split_agent.should_split_cluster(
                        cluster_id=cluster["id"],
                        coherence_score=cluster.get("coherence_score"),
                        cluster_size=cluster.get("size"),
                    )
                    if should_split_result.get("should_split"):
                        split_candidates.append(cluster)
                except Exception as exc:
                    error_msg = f"Error checking split for cluster {cluster['id']}: {str(exc)}"
                    logger.debug("refinement_split_check_error", error=error_msg)
                    result["errors"].append(error_msg)

            # Шаг 3: Выполняем split для кандидатов
            for cluster in split_candidates[:10]:  # Ограничиваем количество split за раз
                try:
                    split_result = await self.split_agent.split_cluster(cluster["id"])
                    if split_result.get("success"):
                        # Валидируем split
                        subclusters = split_result.get("subclusters", [])
                        validation_result = await self.split_agent.validate_split(
                            cluster["id"], subclusters
                        )
                        if validation_result.get("validated"):
                            # Применяем split
                            apply_result = await self.split_agent.apply_split(
                                cluster["id"], subclusters
                            )
                            if apply_result.get("success"):
                                result["clusters_split"] += 1
                                trend_refinement_clusters_split_total.inc()
                                logger.info(
                                    "Cluster split successfully",
                                    cluster_id=cluster["id"],
                                    subclusters_count=len(subclusters),
                                )
                except Exception as exc:
                    error_msg = f"Error splitting cluster {cluster['id']}: {str(exc)}"
                    logger.error("refinement_split_error", error=error_msg, cluster_id=cluster["id"])
                    result["errors"].append(error_msg)

            # Шаг 4: Находим кандидаты на merge
            merge_candidates = await self.merge_agent.find_similar_clusters(limit=50)
            merge_pairs = [mc for mc in merge_candidates if mc.get("merge_score", 0) >= 0.6][:10]

            # Шаг 5: Выполняем merge для кандидатов
            for pair in merge_pairs:
                try:
                    cluster1_id = pair.get("cluster1_id")
                    cluster2_id = pair.get("cluster2_id")
                    
                    # Валидируем merge
                    validation_result = await self.merge_agent.validate_merge(
                        cluster1_id, cluster2_id
                    )
                    if validation_result.get("validated"):
                        # Применяем merge
                        merge_result = await self.merge_agent.merge_clusters(
                            cluster1_id, cluster2_id
                        )
                        if merge_result.get("success"):
                            result["clusters_merged"] += 1
                            trend_refinement_clusters_merged_total.inc()
                            logger.info(
                                "Clusters merged successfully",
                                cluster1_id=cluster1_id,
                                cluster2_id=cluster2_id,
                            )
                except Exception as exc:
                    error_msg = f"Error merging clusters {pair.get('cluster1_id')}, {pair.get('cluster2_id')}: {str(exc)}"
                    logger.error("refinement_merge_error", error=error_msg)
                    result["errors"].append(error_msg)

            # Шаг 6: Создаём подтемы для крупных кластеров (двухуровневая кластеризация)
            if self.subclustering_enabled:
                large_clusters = [
                    c for c in clusters
                    if c.get("size", 0) >= self.subcluster_min_size
                ][:5]  # Ограничиваем количество

                for cluster in large_clusters:
                    try:
                        subclusters_created = await self.create_subclusters(cluster["id"])
                        result["subclusters_created"] += subclusters_created
                    except Exception as exc:
                        error_msg = f"Error creating subclusters for cluster {cluster['id']}: {str(exc)}"
                        logger.debug("refinement_subclustering_error", error=error_msg)
                        result["errors"].append(error_msg)

            duration = time.time() - start_time
            trend_refinement_duration_seconds.observe(duration)
            trend_refinement_clusters_processed.observe(result["clusters_processed"])
            trend_refinement_runs_total.labels(status="success").inc()

            logger.info(
                "Cluster refinement completed",
                duration_seconds=duration,
                **result,
            )

            return result

        except Exception as exc:
            duration = time.time() - start_time
            trend_refinement_duration_seconds.observe(duration)
            trend_refinement_runs_total.labels(status="error").inc()

            logger.error(
                "Cluster refinement failed",
                error=str(exc),
                duration_seconds=duration,
            )
            result["errors"].append(f"Refinement failed: {str(exc)}")
            return result

    async def create_subclusters(
        self,
        cluster_id: str,
    ) -> int:
        """
        Создать подтемы (sub-clusters) для крупного кластера.
        
        Args:
            cluster_id: ID кластера
        
        Returns:
            Количество созданных подкластеров
        """
        if not self.subclustering_enabled or not self.split_agent:
            return 0

        # Проверяем, есть ли уже подкластеры
        query = """
            SELECT COUNT(*) FROM trend_clusters
            WHERE parent_cluster_id = $1;
        """
        try:
            cluster_uuid = uuid.UUID(cluster_id)
        except (ValueError, TypeError):
            return 0

        async with self.db_pool.acquire() as conn:
            existing_subclusters = await conn.fetchval(query, cluster_uuid)

        if existing_subclusters and existing_subclusters > 0:
            # Уже есть подкластеры
            return 0

        # Выполняем sub-clustering
        split_result = await self.split_agent.split_cluster(
            cluster_id,
            algorithm="hdbscan",  # HDBSCAN лучше для sub-clustering
        )

        if not split_result.get("success"):
            return 0

        subclusters = split_result.get("subclusters", [])
        if len(subclusters) < 2:
            return 0

        # Валидируем подкластеры
        validation_result = await self.split_agent.validate_split(cluster_id, subclusters)
        if not validation_result.get("validated"):
            return 0

        # Создаём подкластеры с parent_cluster_id
        created_count = 0
        try:
            async with self.db_pool.acquire() as conn:
                async with conn.transaction():
                    for subcluster in subclusters:
                        new_cluster_id = uuid.uuid4()
                        post_ids = subcluster.get("post_ids", [])

                        if not post_ids:
                            continue

                        # Обновляем trend_cluster_posts: меняем cluster_id на новый
                        update_query = """
                            UPDATE trend_cluster_posts
                            SET cluster_id = $1
                            WHERE cluster_id = $2
                            AND post_id = ANY($3::uuid[]);
                        """
                        await conn.execute(update_query, new_cluster_id, cluster_uuid, post_ids)

                        # Создаём новую запись в trend_clusters как подкластер
                        insert_query = """
                            INSERT INTO trend_clusters (
                                id, cluster_key, status, parent_cluster_id, cluster_level,
                                first_detected_at, last_activity_at
                            )
                            SELECT
                                $1, $2, status, $3, 2,
                                NOW(), NOW()
                            FROM trend_clusters
                            WHERE id = $3;
                        """
                        new_cluster_key = f"sub_{cluster_id[:8]}_{subcluster.get('label')}"
                        await conn.execute(
                            insert_query,
                            new_cluster_id,
                            new_cluster_key[:64],
                            cluster_uuid,
                        )

                        created_count += 1

            logger.info(
                "Subclusters created",
                cluster_id=cluster_id,
                subclusters_count=created_count,
            )

        except Exception as exc:
            logger.error(
                "Failed to create subclusters",
                error=str(exc),
                cluster_id=cluster_id,
            )

        return created_count

    async def _get_clusters_for_refinement(self) -> List[Dict[str, Any]]:
        """Получить кластеры для рефайнмента."""
        if not self.db_pool:
            return []

        # Получаем активные кластеры, которые не обрабатывались недавно
        hours_ago = datetime.now(timezone.utc) - timedelta(hours=self.refinement_interval_hours)
        
        query = """
            SELECT 
                id,
                cluster_key,
                coherence_score,
                intra_cluster_similarity,
                last_refinement_at,
                (SELECT COUNT(*) FROM trend_cluster_posts WHERE cluster_id = trend_clusters.id) as size
            FROM trend_clusters
            WHERE status = 'active'
            AND cluster_level = 1  -- Только основные кластеры (не подкластеры)
            AND (
                last_refinement_at IS NULL
                OR last_refinement_at < $1
            )
            AND (SELECT COUNT(*) FROM trend_cluster_posts WHERE cluster_id = trend_clusters.id) >= $2
            ORDER BY last_activity_at DESC
            LIMIT $3;
        """
        async with self.db_pool.acquire() as conn:
            records = await conn.fetch(
                query,
                hours_ago,
                self.min_cluster_size_for_refinement,
                self.max_clusters_per_run,
            )

        return [
            {
                "id": str(record.get("id")),
                "cluster_key": record.get("cluster_key"),
                "coherence_score": record.get("coherence_score"),
                "intra_cluster_similarity": record.get("intra_cluster_similarity"),
                "last_refinement_at": record.get("last_refinement_at"),
                "size": record.get("size") or 0,
            }
            for record in records
        ]

    async def _evaluate_cluster_metrics(self, cluster_id: str) -> None:
        """Оценить метрики когерентности для кластера и обновить в БД."""
        if not self.coherence_metrics or not self.db_pool:
            return

        try:
            cluster_uuid = uuid.UUID(cluster_id)
        except (ValueError, TypeError):
            return

        # Получаем embedding постов кластера
        embeddings_result = await self._get_cluster_embeddings_for_metrics(cluster_id)
        if not embeddings_result:
            return

        embeddings = embeddings_result.get("embeddings", [])
        if len(embeddings) < 2:
            return

        # Получаем keywords кластера
        top_keywords, all_post_keywords = await self.coherence_metrics.get_cluster_keywords(cluster_id)

        # Вычисляем метрики
        metrics = await self.coherence_metrics.calculate_all_metrics(
            cluster_id=cluster_id,
            cluster_embeddings=embeddings,
            cluster_keywords=top_keywords,
            all_clusters_embeddings=None,  # Для silhouette нужны все кластеры, пока упрощаем
        )

        # Обновляем метрики в БД
        update_query = """
            UPDATE trend_clusters
            SET coherence_score = COALESCE($1, coherence_score),
                intra_cluster_similarity = COALESCE($2, intra_cluster_similarity),
                npmi_score = COALESCE($3, npmi_score),
                silhouette_score = COALESCE($4, silhouette_score),
                last_refinement_at = NOW()
            WHERE id = $5;
        """
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                update_query,
                metrics.get("intra_cluster_similarity"),  # Используем как coherence_score
                metrics.get("intra_cluster_similarity"),
                metrics.get("npmi_score"),
                metrics.get("silhouette_score"),
                cluster_uuid,
            )

    async def _get_cluster_embeddings_for_metrics(self, cluster_id: str) -> Optional[Dict[str, Any]]:
        """
        Получить embedding постов кластера для вычисления метрик.
        
        Context7: Используем retrieve_vectors для batch получения embeddings из Qdrant.
        """
        if not self.db_pool:
            return {"embeddings": [], "post_ids": []}
        
        try:
            cluster_uuid = uuid.UUID(cluster_id)
        except (ValueError, TypeError):
            return {"embeddings": [], "post_ids": []}
        
        # Получаем post_ids из кластера
        query = """
            SELECT post_id
            FROM trend_cluster_posts
            WHERE cluster_id = $1
            ORDER BY COALESCE(posted_at, created_at) DESC
            LIMIT 100;
        """
        async with self.db_pool.acquire() as conn:
            records = await conn.fetch(query, cluster_uuid)
        
        post_ids = [str(record.get("post_id")) for record in records if record.get("post_id")]
        
        if not post_ids:
            return {"embeddings": [], "post_ids": []}
        
        # Context7: Получаем tenant_id для определения коллекции
        tenant_query = """
            SELECT DISTINCT 
                COALESCE(
                    (SELECT u.tenant_id::text FROM users u 
                     JOIN user_channel uc ON uc.user_id = u.id 
                     WHERE uc.channel_id = c.id 
                     LIMIT 1),
                    CAST(pe.data->>'tenant_id' AS text),
                    CAST(c.settings->>'tenant_id' AS text),
                    'default'
                ) as tenant_id
            FROM trend_cluster_posts tcp
            JOIN posts p ON p.id = tcp.post_id
            JOIN channels c ON c.id = p.channel_id
            LEFT JOIN post_enrichment pe ON pe.post_id = p.id AND pe.kind = 'tags'
            WHERE tcp.cluster_id = $1
            LIMIT 1;
        """
        async with self.db_pool.acquire() as conn:
            tenant_record = await conn.fetchrow(tenant_query, cluster_uuid)
        
        tenant_id = "default"
        if tenant_record and tenant_record.get("tenant_id"):
            tenant_id = str(tenant_record.get("tenant_id"))
        
        # Context7: Коллекция per-tenant: t{tenant_id}_posts
        collection_name = f"t{tenant_id}_posts"
        
        embeddings = []
        valid_post_ids = []
        
        if self.qdrant_client:
            try:
                # Получаем векторы из Qdrant по post_id (post_id = vector_id в Qdrant)
                qdrant_points = await self.qdrant_client.retrieve_vectors(
                    collection_name=collection_name,
                    vector_ids=post_ids,
                    with_vectors=True,
                    with_payload=False
                )
                
                # Сортируем embeddings в том же порядке, что и post_ids
                embedding_map = {point['id']: point['vector'] for point in qdrant_points if point.get('vector')}
                
                for post_id in post_ids:
                    if post_id in embedding_map:
                        embeddings.append(embedding_map[post_id])
                        valid_post_ids.append(post_id)
                
                logger.debug(
                    "Retrieved embeddings for metrics",
                    cluster_id=cluster_id,
                    collection=collection_name,
                    requested_count=len(post_ids),
                    retrieved_count=len(embeddings),
                    tenant_id=tenant_id
                )
                
            except Exception as exc:
                logger.warning(
                    "Failed to retrieve embeddings for metrics",
                    cluster_id=cluster_id,
                    error=str(exc),
                    exc_info=True
                )
                # Fallback: возвращаем пустые embeddings
                embeddings = []
                valid_post_ids = []
        
        return {
            "embeddings": embeddings,
            "post_ids": valid_post_ids,
        }

    async def start_periodic_refinement(self):
        """
        Запустить периодический рефайнмент кластеров.
        
        Context7: Первый запуск выполняется сразу, затем по расписанию.
        """
        if not self.refinement_enabled:
            logger.info("Trend refinement service disabled, skipping periodic refinement")
            return

        await self.initialize()

        interval_seconds = self.refinement_interval_hours * 3600

        logger.info(
            "Starting periodic cluster refinement",
            interval_hours=self.refinement_interval_hours,
            interval_seconds=interval_seconds,
        )

        # Context7: Первый запуск выполняется сразу, затем по расписанию
        first_run = True
        
        while True:
            try:
                if not first_run:
                    await asyncio.sleep(interval_seconds)
                else:
                    first_run = False
                
                logger.info("Starting scheduled cluster refinement")
                result = await self.refine_clusters()
                logger.info(
                    "Scheduled cluster refinement completed",
                    clusters_processed=result.get("clusters_processed", 0),
                    clusters_split=result.get("clusters_split", 0),
                    clusters_merged=result.get("clusters_merged", 0),
                    subclusters_created=result.get("subclusters_created", 0),
                )
            except Exception as exc:
                logger.error(
                    "Error in periodic cluster refinement",
                    error=str(exc),
                    exc_info=True,
                )
                await asyncio.sleep(60)  # Пауза при ошибке

    async def close(self):
        """Закрыть соединения."""
        if self.db_pool:
            await self.db_pool.close()
        if self.qdrant_client:
            await self.qdrant_client.disconnect()
        logger.info("TrendRefinementService closed")


def create_refinement_service(
    database_url: str,
    qdrant_url: str,
) -> TrendRefinementService:
    """Создание экземпляра TrendRefinementService."""
    return TrendRefinementService(database_url=database_url, qdrant_url=qdrant_url)

