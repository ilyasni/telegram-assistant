"""
Trend Split Agent для автоматического разделения низкокогерентных кластеров.

Context7: Обнаруживает кластеры с низкой когерентностью и разделяет их на
тематически однородные подкластеры с использованием HDBSCAN/K-Means.
"""

from __future__ import annotations

import os
import uuid
import json
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import httpx
import numpy as np
import structlog
from sklearn.cluster import KMeans

try:
    import hdbscan
    HDBSCAN_AVAILABLE = True
except ImportError:
    HDBSCAN_AVAILABLE = False
    hdbscan = None

from config import settings
from integrations.qdrant_client import QdrantClient

logger = structlog.get_logger()


# ============================================================================
# TREND SPLIT AGENT
# ============================================================================


class TrendSplitAgent:
    """
    Агент разделения кластеров:
    - определяет, нужно ли разделять кластер (низкая когерентность),
    - выполняет sub-clustering внутри кластера (HDBSCAN/K-Means),
    - валидирует подкластеры через LLM,
    - применяет разделение: создаёт новые кластеры, перераспределяет посты.
    """

    def __init__(
        self,
        db_pool: Optional[asyncpg.Pool] = None,
        qdrant_client: Optional[QdrantClient] = None,
    ):
        """
        Инициализация TrendSplitAgent.
        
        Args:
            db_pool: Пул подключений к БД
            qdrant_client: Клиент Qdrant для работы с векторами
        """
        self.db_pool = db_pool
        self.qdrant_client = qdrant_client
        
        self.split_enabled = os.getenv("TREND_SPLIT_ENABLED", "true").lower() == "true"
        self.min_coherence_for_split = float(os.getenv("TREND_MIN_COHERENCE_FOR_SPLIT", "0.3"))
        self.min_cluster_size_for_split = int(os.getenv("TREND_MIN_CLUSTER_SIZE_FOR_SPLIT", "5"))
        self.min_subclusters = int(os.getenv("TREND_MIN_SUBCLUSTERS", "2"))
        self.max_subclusters = int(os.getenv("TREND_MAX_SUBCLUSTERS", "5"))
        self.llm_validation_enabled = os.getenv("TREND_SPLIT_LLM_VALIDATION", "true").lower() == "true"

    async def should_split_cluster(
        self,
        cluster_id: str,
        coherence_score: Optional[float] = None,
        cluster_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Определить, нужно ли разделять кластер.
        
        Args:
            cluster_id: ID кластера
            coherence_score: Оценка когерентности кластера (опционально)
            cluster_size: Размер кластера (опционально)
        
        Returns:
            Dict с полями:
            - should_split: bool - нужно ли разделять
            - reasoning: str - причина решения
        """
        if not self.split_enabled:
            return {
                "should_split": False,
                "reasoning": "Split agent disabled",
            }

        if not self.db_pool:
            return {
                "should_split": False,
                "reasoning": "DB pool unavailable",
            }

        try:
            cluster_uuid = uuid.UUID(cluster_id)
        except (ValueError, TypeError):
            return {
                "should_split": False,
                "reasoning": "Invalid cluster_id",
            }

        # Получаем метрики кластера, если не предоставлены
        if coherence_score is None or cluster_size is None:
            query = """
                SELECT coherence_score, intra_cluster_similarity,
                       (SELECT COUNT(*) FROM trend_cluster_posts WHERE cluster_id = $1) as size
                FROM trend_clusters
                WHERE id = $1;
            """
            async with self.db_pool.acquire() as conn:
                record = await conn.fetchrow(query, cluster_uuid)

            if not record:
                return {
                    "should_split": False,
                    "reasoning": "Cluster not found",
                }

            if coherence_score is None:
                coherence_score = record.get("coherence_score")
                # Если coherence_score нет, используем intra_cluster_similarity
                if coherence_score is None:
                    coherence_score = record.get("intra_cluster_similarity")

            if cluster_size is None:
                cluster_size = record.get("size") or 0

        # Проверяем условия для разделения
        if cluster_size < self.min_cluster_size_for_split:
            return {
                "should_split": False,
                "reasoning": f"Cluster too small: {cluster_size} < {self.min_cluster_size_for_split}",
            }

        if coherence_score is None:
            # Если метрика не вычислена, не разделяем
            return {
                "should_split": False,
                "reasoning": "Coherence score not available",
            }

        if coherence_score >= self.min_coherence_for_split:
            return {
                "should_split": False,
                "reasoning": f"Coherence score acceptable: {coherence_score:.3f} >= {self.min_coherence_for_split}",
            }

        return {
            "should_split": True,
            "reasoning": f"Low coherence: {coherence_score:.3f} < {self.min_coherence_for_split}, size: {cluster_size}",
        }

    async def split_cluster(
        self,
        cluster_id: str,
        embeddings: Optional[List[List[float]]] = None,
        algorithm: str = "hdbscan",
    ) -> Dict[str, Any]:
        """
        Разделить кластер на подкластеры.
        
        Args:
            cluster_id: ID кластера для разделения
            embeddings: Embedding постов кластера (опционально, если None - получаем из БД)
            algorithm: Алгоритм кластеризации ("hdbscan" или "kmeans")
        
        Returns:
            Dict с полями:
            - success: bool - успешно ли выполнено разделение
            - subclusters: List[Dict] - список подкластеров с метками
            - reasoning: str - объяснение результата
        """
        if not self.db_pool:
            return {
                "success": False,
                "subclusters": [],
                "reasoning": "DB pool unavailable",
            }

        try:
            cluster_uuid = uuid.UUID(cluster_id)
        except (ValueError, TypeError):
            return {
                "success": False,
                "subclusters": [],
                "reasoning": "Invalid cluster_id",
            }

        # Получаем embedding постов, если не предоставлены
        if embeddings is None:
            embeddings_result = await self._get_cluster_embeddings(cluster_id)
            if not embeddings_result:
                return {
                    "success": False,
                    "subclusters": [],
                    "reasoning": "No embeddings found for cluster",
                }
            embeddings = embeddings_result.get("embeddings", [])
            post_ids = embeddings_result.get("post_ids", [])

            if len(embeddings) < self.min_cluster_size_for_split:
                return {
                    "success": False,
                    "subclusters": [],
                    "reasoning": f"Not enough posts for splitting: {len(embeddings)} < {self.min_cluster_size_for_split}",
                }
        else:
            # Если embeddings предоставлены, получаем post_ids отдельно
            post_ids_result = await self._get_cluster_post_ids(cluster_id)
            post_ids = post_ids_result.get("post_ids", [])
            if len(post_ids) != len(embeddings):
                # Урезаем до минимальной длины
                min_len = min(len(post_ids), len(embeddings))
                post_ids = post_ids[:min_len]
                embeddings = embeddings[:min_len]

        if len(embeddings) < 2:
            return {
                "success": False,
                "subclusters": [],
                "reasoning": "Not enough posts for clustering",
            }

        # Выполняем кластеризацию
        try:
            if algorithm == "hdbscan" and HDBSCAN_AVAILABLE:
                labels = await self._hdbscan_clustering(embeddings)
            elif algorithm == "kmeans":
                labels = await self._kmeans_clustering(embeddings)
            else:
                # Fallback на K-Means
                labels = await self._kmeans_clustering(embeddings)
        except Exception as exc:
            logger.error(
                "split_agent_clustering_failed",
                error=str(exc),
                cluster_id=cluster_id,
                algorithm=algorithm,
            )
            return {
                "success": False,
                "subclusters": [],
                "reasoning": f"Clustering failed: {str(exc)}",
            }

        # Группируем посты по кластерам
        subclusters = {}
        noise_posts = []

        for idx, (post_id, label) in enumerate(zip(post_ids, labels)):
            if label == -1:  # Шум (для HDBSCAN)
                noise_posts.append(post_id)
            else:
                if label not in subclusters:
                    subclusters[label] = {
                        "post_ids": [],
                        "embeddings": [],
                    }
                subclusters[label]["post_ids"].append(post_id)
                if idx < len(embeddings):
                    subclusters[label]["embeddings"].append(embeddings[idx])

        # Фильтруем слишком маленькие подкластеры
        valid_subclusters = {}
        for label, data in subclusters.items():
            if len(data["post_ids"]) >= 2:  # Минимум 2 поста в подкластере
                valid_subclusters[label] = data

        if len(valid_subclusters) < self.min_subclusters:
            return {
                "success": False,
                "subclusters": [],
                "reasoning": f"Too few valid subclusters: {len(valid_subclusters)} < {self.min_subclusters}",
            }

        # Формируем результат
        result_subclusters = []
        for label, data in valid_subclusters.items():
            result_subclusters.append({
                "label": label,
                "post_ids": data["post_ids"],
                "size": len(data["post_ids"]),
            })

        return {
            "success": True,
            "subclusters": result_subclusters,
            "noise_posts": noise_posts,
            "reasoning": f"Split into {len(result_subclusters)} subclusters",
        }

    async def validate_split(
        self,
        cluster_id: str,
        subclusters: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Валидировать разделение кластера через LLM.
        
        Args:
            cluster_id: ID исходного кластера
            subclusters: Список подкластеров для валидации
        
        Returns:
            Dict с полями:
            - validated: bool - валидировано ли разделение
            - reasoning: str - объяснение LLM
        """
        if not self.llm_validation_enabled:
            return {
                "validated": True,
                "reasoning": "LLM validation disabled",
            }

        # Получаем информацию о кластере и подкластерах
        cluster_info = await self._get_cluster_info(cluster_id)
        if not cluster_info:
            return {
                "validated": False,
                "reasoning": "Cluster info not available",
            }

        # Получаем примеры постов для каждого подкластера
        subcluster_samples = []
        for subcluster in subclusters[:self.max_subclusters]:
            post_ids = subcluster.get("post_ids", [])[:5]  # До 5 постов для примера
            samples = await self._get_posts_samples(post_ids)
            subcluster_samples.append({
                "label": subcluster.get("label"),
                "size": subcluster.get("size"),
                "samples": samples,
            })

        try:
            llm_result = await self._call_split_validation_llm(
                cluster_info=cluster_info,
                subclusters=subcluster_samples,
            )

            validated = llm_result.get("validated", False)
            reasoning = llm_result.get("reasoning", "")

            return {
                "validated": validated,
                "reasoning": reasoning,
            }
        except Exception as exc:
            logger.debug(
                "split_agent_llm_validation_failed",
                error=str(exc),
                cluster_id=cluster_id,
            )
            # При ошибке LLM валидируем разделение (продолжаем)
            return {
                "validated": True,
                "reasoning": f"LLM validation error: {str(exc)}",
            }

    async def apply_split(
        self,
        cluster_id: str,
        subclusters: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Применить разделение кластера: создать новые кластеры, перераспределить посты.
        
        Args:
            cluster_id: ID исходного кластера
            subclusters: Список подкластеров для создания
        
        Returns:
            Dict с полями:
            - success: bool - успешно ли применено
            - new_cluster_ids: List[str] - ID созданных кластеров
            - reasoning: str - объяснение
        """
        if not self.db_pool:
            return {
                "success": False,
                "new_cluster_ids": [],
                "reasoning": "DB pool unavailable",
            }

        try:
            cluster_uuid = uuid.UUID(cluster_id)
        except (ValueError, TypeError):
            return {
                "success": False,
                "new_cluster_ids": [],
                "reasoning": "Invalid cluster_id",
            }

        # Получаем информацию о исходном кластере
        cluster_info = await self._get_cluster_info(cluster_id)
        if not cluster_info:
            return {
                "success": False,
                "new_cluster_ids": [],
                "reasoning": "Cluster info not available",
            }

        new_cluster_ids = []

        try:
            async with self.db_pool.acquire() as conn:
                async with conn.transaction():
                    # Создаём новые кластеры для каждого подкластера
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
                        await conn.execute(
                            update_query,
                            new_cluster_id,
                            cluster_uuid,
                            post_ids,
                        )

                        # Создаём новую запись в trend_clusters на основе исходной
                        insert_query = """
                            INSERT INTO trend_clusters (
                                id, cluster_key, status, label, summary, keywords,
                                primary_topic, topics, coherence_score, source_diversity,
                                first_detected_at, last_activity_at, parent_cluster_id
                            )
                            SELECT
                                $1, $2, status, label, summary, keywords,
                                primary_topic, topics, coherence_score, source_diversity,
                                NOW(), NOW(), $3
                            FROM trend_clusters
                            WHERE id = $3;
                        """
                        new_cluster_key = f"{cluster_info.get('cluster_key', '')}_split_{subcluster.get('label')}"
                        await conn.execute(
                            insert_query,
                            new_cluster_id,
                            new_cluster_key[:64],
                            cluster_uuid,
                        )

                        new_cluster_ids.append(str(new_cluster_id))

                    # Помечаем исходный кластер как archived или удаляем
                    archive_query = """
                        UPDATE trend_clusters
                        SET status = 'archived'
                        WHERE id = $1;
                    """
                    await conn.execute(archive_query, cluster_uuid)

            return {
                "success": True,
                "new_cluster_ids": new_cluster_ids,
                "reasoning": f"Split into {len(new_cluster_ids)} clusters",
            }

        except Exception as exc:
            logger.error(
                "split_agent_apply_failed",
                error=str(exc),
                cluster_id=cluster_id,
            )
            return {
                "success": False,
                "new_cluster_ids": [],
                "reasoning": f"Failed to apply split: {str(exc)}",
            }

    async def _hdbscan_clustering(self, embeddings: List[List[float]]) -> List[int]:
        """Выполнить HDBSCAN кластеризацию."""
        if not HDBSCAN_AVAILABLE:
            raise ImportError("HDBSCAN not available")

        embeddings_array = np.array(embeddings)
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=max(2, len(embeddings) // 5),
            min_samples=1,
            metric="cosine",
        )
        labels = clusterer.fit_predict(embeddings_array)
        return labels.tolist()

    async def _kmeans_clustering(self, embeddings: List[List[float]]) -> List[int]:
        """Выполнить K-Means кластеризацию."""
        embeddings_array = np.array(embeddings)
        
        # Определяем количество кластеров
        n_clusters = min(
            self.max_subclusters,
            max(self.min_subclusters, len(embeddings) // 3),
        )

        kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        labels = kmeans.fit_predict(embeddings_array)
        return labels.tolist()

    async def _get_cluster_embeddings(self, cluster_id: str) -> Dict[str, Any]:
        """Получить embedding постов кластера."""
        if not self.db_pool:
            return {"embeddings": [], "post_ids": []}

        try:
            cluster_uuid = uuid.UUID(cluster_id)
        except (ValueError, TypeError):
            return {"embeddings": [], "post_ids": []}

        # Получаем post_ids из trend_cluster_posts
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
        
        # Context7: Получаем embeddings из Qdrant по post_id
        # Используем per-tenant коллекции: t{tenant_id}_posts
        embeddings = []
        valid_post_ids = []
        
        if self.qdrant_client and post_ids:
            try:
                # Получаем tenant_id для постов из кластера
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
                    if tenant_id == "default":
                        # Пробуем получить из первого поста
                        first_post_query = """
                            SELECT p.id
                            FROM trend_cluster_posts tcp
                            JOIN posts p ON p.id = tcp.post_id
                            WHERE tcp.cluster_id = $1
                            LIMIT 1;
                        """
                        first_post_record = await conn.fetchrow(first_post_query, cluster_uuid)
                        if first_post_record:
                            # Используем post_id как vector_id в Qdrant (Context7 best practice)
                            pass
                
                # Context7: В Qdrant post_id используется как vector_id
                # Коллекция: t{tenant_id}_posts
                collection_name = f"t{tenant_id}_posts"
                
                # Получаем векторы из Qdrant по post_id
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
                    "Retrieved embeddings from Qdrant",
                    cluster_id=cluster_id,
                    collection=collection_name,
                    requested_count=len(post_ids),
                    retrieved_count=len(embeddings),
                    tenant_id=tenant_id
                )
                
            except Exception as exc:
                logger.warning(
                    "Failed to retrieve embeddings from Qdrant",
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

    async def _get_cluster_post_ids(self, cluster_id: str) -> Dict[str, List[str]]:
        """Получить список post_id кластера."""
        if not self.db_pool:
            return {"post_ids": []}

        try:
            cluster_uuid = uuid.UUID(cluster_id)
        except (ValueError, TypeError):
            return {"post_ids": []}

        query = """
            SELECT post_id
            FROM trend_cluster_posts
            WHERE cluster_id = $1;
        """
        async with self.db_pool.acquire() as conn:
            records = await conn.fetch(query, cluster_uuid)

        post_ids = [str(record.get("post_id")) for record in records if record.get("post_id")]
        return {"post_ids": post_ids}

    async def _get_cluster_info(self, cluster_id: str) -> Optional[Dict[str, Any]]:
        """Получить информацию о кластере."""
        if not self.db_pool:
            return None

        try:
            cluster_uuid = uuid.UUID(cluster_id)
        except (ValueError, TypeError):
            return None

        query = """
            SELECT id, cluster_key, label, summary, keywords, primary_topic
            FROM trend_clusters
            WHERE id = $1;
        """
        async with self.db_pool.acquire() as conn:
            record = await conn.fetchrow(query, cluster_uuid)

        if not record:
            return None

        keywords = record.get("keywords")
        if isinstance(keywords, str):
            try:
                keywords = json.loads(keywords)
            except (json.JSONDecodeError, TypeError):
                keywords = []

        return {
            "id": str(record.get("id")),
            "cluster_key": record.get("cluster_key"),
            "label": record.get("label"),
            "summary": record.get("summary"),
            "keywords": keywords or [],
            "primary_topic": record.get("primary_topic"),
        }

    async def _get_posts_samples(self, post_ids: List[str]) -> List[str]:
        """Получить примеры текстов постов."""
        if not self.db_pool or not post_ids:
            return []

        try:
            post_uuids = [uuid.UUID(pid) for pid in post_ids]
        except (ValueError, TypeError):
            return []

        query = """
            SELECT text
            FROM posts
            WHERE id = ANY($1::uuid[])
            LIMIT 5;
        """
        async with self.db_pool.acquire() as conn:
            records = await conn.fetch(query, post_uuids)

        return [
            (record.get("text") or "")[:200]  # Ограничиваем длину
            for record in records
            if record.get("text")
        ]

    async def _call_split_validation_llm(
        self,
        cluster_info: Dict[str, Any],
        subclusters: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Вызвать LLM для валидации разделения кластера."""
        api_base = (
            getattr(settings, "openai_api_base", None)
            or os.getenv("OPENAI_API_BASE")
            or os.getenv("GIGACHAT_PROXY_URL")
            or "http://gpt2giga-proxy:8090"
        )

        system_message = (
            "Ты — агент валидации разделения кластеров трендов. "
            "Оцени, правильно ли исходный кластер был разделён на подкластеры. "
            "Подкластеры должны быть тематически однородными и отличаться друг от друга. "
            "Верни JSON с полями 'validated' (true/false) и 'reasoning' (объяснение)."
        )

        user_message = (
            f"Исходный кластер: {cluster_info.get('label') or cluster_info.get('primary_topic')}\n"
            f"Описание: {cluster_info.get('summary', '')[:300]}\n"
            f"Ключевые слова: {', '.join(cluster_info.get('keywords', [])[:10])}\n\n"
            f"Подкластеры:\n"
        )

        for subcluster in subclusters:
            samples = subcluster.get("samples", [])
            samples_text = "\n".join([f"- {s}" for s in samples[:3]])
            user_message += (
                f"\nПодкластер {subcluster.get('label')} ({subcluster.get('size')} постов):\n"
                f"{samples_text}\n"
            )

        user_message += "\nВалидно ли это разделение? Подкластеры тематически однородны и отличаются друг от друга?"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{api_base}/v1/chat/completions",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', 'dummy')}",
                    },
                    json={
                        "model": os.getenv("TREND_SPLIT_LLM_MODEL", "GigaChat"),
                        "messages": [
                            {"role": "system", "content": system_message},
                            {"role": "user", "content": user_message},
                        ],
                        "max_tokens": 200,
                        "temperature": 0.3,
                    },
                )
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]

                # Парсим JSON ответ
                try:
                    result = json.loads(content)
                except json.JSONDecodeError:
                    # Если не JSON, пытаемся извлечь решение из текста
                    validated = "true" in content.lower() or "valid" in content.lower()
                    result = {
                        "validated": validated,
                        "reasoning": content[:200],
                    }

                return result

        except Exception as exc:
            logger.debug("split_agent_llm_call_failed", error=str(exc))
            raise


def create_split_agent(
    db_pool: Optional[asyncpg.Pool] = None,
    qdrant_client: Optional[QdrantClient] = None,
) -> TrendSplitAgent:
    """Создание экземпляра TrendSplitAgent."""
    return TrendSplitAgent(db_pool=db_pool, qdrant_client=qdrant_client)

