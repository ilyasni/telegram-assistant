"""
Trend Merge Agent для автоматического слияния похожих кластеров.

Context7: Находит похожие/мелкие кластеры и объединяет их в один
тематически однородный кластер.
"""

from __future__ import annotations

import os
import uuid
import json
from typing import Any, Dict, List, Optional, Set, Tuple

import asyncpg
import httpx
import numpy as np
import structlog

from config import settings
from integrations.qdrant_client import QdrantClient
from api.worker.trends_keyword_extractor import TrendKeywordExtractor

logger = structlog.get_logger()


# ============================================================================
# TREND MERGE AGENT
# ============================================================================


class TrendMergeAgent:
    """
    Агент слияния кластеров:
    - находит похожие/мелкие кластеры (пересекающиеся keywords, близкие центроиды),
    - валидирует слияние через LLM,
    - объединяет кластеры в БД и Qdrant.
    """

    def __init__(
        self,
        db_pool: Optional[asyncpg.Pool] = None,
        qdrant_client: Optional[QdrantClient] = None,
        keyword_extractor: Optional[TrendKeywordExtractor] = None,
    ):
        """
        Инициализация TrendMergeAgent.
        
        Args:
            db_pool: Пул подключений к БД
            qdrant_client: Клиент Qdrant для работы с векторами
            keyword_extractor: Экстрактор ключевых слов для вычисления overlap
        """
        self.db_pool = db_pool
        self.qdrant_client = qdrant_client
        self.keyword_extractor = keyword_extractor
        
        self.merge_enabled = os.getenv("TREND_MERGE_ENABLED", "true").lower() == "true"
        self.min_keyword_overlap = float(os.getenv("TREND_MERGE_MIN_KEYWORD_OVERLAP", "0.5"))
        self.min_centroid_similarity = float(os.getenv("TREND_MERGE_MIN_CENTROID_SIMILARITY", "0.85"))
        self.min_cluster_size_for_merge = int(os.getenv("TREND_MERGE_MIN_CLUSTER_SIZE", "2"))
        self.max_cluster_size = int(os.getenv("TREND_MERGE_MAX_CLUSTER_SIZE", "50"))
        self.llm_validation_enabled = os.getenv("TREND_MERGE_LLM_VALIDATION", "true").lower() == "true"

    async def find_similar_clusters(
        self,
        cluster_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Найти похожие кластеры для слияния.
        
        Args:
            cluster_id: ID конкретного кластера для поиска похожих (опционально)
            limit: Максимальное количество кластеров для проверки
        
        Returns:
            Список кандидатов на слияние с полями:
            - cluster1_id: str
            - cluster2_id: str
            - keyword_overlap: float
            - centroid_similarity: float
            - merge_score: float
        """
        if not self.merge_enabled or not self.db_pool:
            return []

        try:
            if cluster_id:
                cluster_uuid = uuid.UUID(cluster_id)
            else:
                cluster_uuid = None
        except (ValueError, TypeError):
            return []

        # Получаем активные кластеры с метаданными
        if cluster_id:
            query = """
                SELECT id, cluster_key, keywords, primary_topic, trend_embedding,
                       (SELECT COUNT(*) FROM trend_cluster_posts WHERE cluster_id = trend_clusters.id) as size
                FROM trend_clusters
                WHERE status = 'active' AND id != $1
                ORDER BY last_activity_at DESC
                LIMIT $2;
            """
            params = (cluster_uuid, limit)
        else:
            query = """
                SELECT id, cluster_key, keywords, primary_topic, trend_embedding,
                       (SELECT COUNT(*) FROM trend_cluster_posts WHERE cluster_id = trend_clusters.id) as size
                FROM trend_clusters
                WHERE status = 'active'
                ORDER BY last_activity_at DESC
                LIMIT $2;
            """
            params = (limit,)

        async with self.db_pool.acquire() as conn:
            records = await conn.fetch(query, *params)

        if not records:
            return []

        # Если указан конкретный кластер, получаем его данные
        if cluster_id:
            target_query = """
                SELECT id, cluster_key, keywords, primary_topic, trend_embedding,
                       (SELECT COUNT(*) FROM trend_cluster_posts WHERE cluster_id = trend_clusters.id) as size
                FROM trend_clusters
                WHERE id = $1;
            """
            async with self.db_pool.acquire() as conn:
                target_record = await conn.fetchrow(target_query, cluster_uuid)

            if not target_record:
                return []

            target_cluster = self._parse_cluster_record(target_record)
            candidates = [self._parse_cluster_record(r) for r in records]
        else:
            # Проверяем все пары кластеров
            clusters = [self._parse_cluster_record(r) for r in records]
            candidates_pairs = []

            for i, cluster1 in enumerate(clusters):
                for cluster2 in clusters[i + 1 :]:
                    if cluster1["size"] < self.min_cluster_size_for_merge and cluster2["size"] < self.min_cluster_size_for_merge:
                        # Оба слишком маленькие - кандидаты на слияние
                        overlap = await self._calculate_keyword_overlap(
                            cluster1["keywords"], cluster2["keywords"]
                        )
                        similarity = await self._calculate_centroid_similarity(
                            cluster1["embedding"], cluster2["embedding"]
                        )

                        if overlap >= self.min_keyword_overlap or similarity >= self.min_centroid_similarity:
                            merge_score = (overlap + similarity) / 2.0
                            candidates_pairs.append({
                                "cluster1_id": cluster1["id"],
                                "cluster2_id": cluster2["id"],
                                "keyword_overlap": overlap,
                                "centroid_similarity": similarity,
                                "merge_score": merge_score,
                            })

            return sorted(candidates_pairs, key=lambda x: x["merge_score"], reverse=True)

        # Для конкретного кластера проверяем только его похожесть с кандидатами
        candidates_pairs = []
        for candidate in candidates:
            overlap = await self._calculate_keyword_overlap(
                target_cluster["keywords"], candidate["keywords"]
            )
            similarity = await self._calculate_centroid_similarity(
                target_cluster["embedding"], candidate["embedding"]
            )

            # Проверяем условия для слияния
            if (
                (overlap >= self.min_keyword_overlap or similarity >= self.min_centroid_similarity)
                and (target_cluster["size"] < self.min_cluster_size_for_merge or candidate["size"] < self.min_cluster_size_for_merge)
            ):
                merge_score = (overlap + similarity) / 2.0
                candidates_pairs.append({
                    "cluster1_id": target_cluster["id"],
                    "cluster2_id": candidate["id"],
                    "keyword_overlap": overlap,
                    "centroid_similarity": similarity,
                    "merge_score": merge_score,
                })

        return sorted(candidates_pairs, key=lambda x: x["merge_score"], reverse=True)

    async def validate_merge(
        self,
        cluster1_id: str,
        cluster2_id: str,
    ) -> Dict[str, Any]:
        """
        Валидировать слияние двух кластеров через LLM.
        
        Args:
            cluster1_id: ID первого кластера
            cluster2_id: ID второго кластера
        
        Returns:
            Dict с полями:
            - validated: bool - валидировано ли слияние
            - reasoning: str - объяснение LLM
        """
        if not self.llm_validation_enabled:
            return {
                "validated": True,
                "reasoning": "LLM validation disabled",
            }

        # Получаем информацию о кластерах
        cluster1_info = await self._get_cluster_info(cluster1_id)
        cluster2_info = await self._get_cluster_info(cluster2_id)

        if not cluster1_info or not cluster2_info:
            return {
                "validated": False,
                "reasoning": "Cluster info not available",
            }

        try:
            llm_result = await self._call_merge_validation_llm(
                cluster1_info=cluster1_info,
                cluster2_info=cluster2_info,
            )

            validated = llm_result.get("validated", False)
            reasoning = llm_result.get("reasoning", "")

            return {
                "validated": validated,
                "reasoning": reasoning,
            }
        except Exception as exc:
            logger.debug(
                "merge_agent_llm_validation_failed",
                error=str(exc),
                cluster1_id=cluster1_id,
                cluster2_id=cluster2_id,
            )
            # При ошибке LLM валидируем слияние (продолжаем)
            return {
                "validated": True,
                "reasoning": f"LLM validation error: {str(exc)}",
            }

    async def merge_clusters(
        self,
        cluster1_id: str,
        cluster2_id: str,
        keep_cluster_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Объединить два кластера в один.
        
        Args:
            cluster1_id: ID первого кластера
            cluster2_id: ID второго кластера
            keep_cluster_id: ID кластера, который сохранить (если None - выбирается автоматически)
        
        Returns:
            Dict с полями:
            - success: bool - успешно ли выполнено слияние
            - merged_cluster_id: str - ID объединённого кластера
            - reasoning: str - объяснение
        """
        if not self.db_pool:
            return {
                "success": False,
                "merged_cluster_id": None,
                "reasoning": "DB pool unavailable",
            }

        try:
            cluster1_uuid = uuid.UUID(cluster1_id)
            cluster2_uuid = uuid.UUID(cluster2_id)
        except (ValueError, TypeError):
            return {
                "success": False,
                "merged_cluster_id": None,
                "reasoning": "Invalid cluster_id",
            }

        # Определяем, какой кластер сохранить
        if keep_cluster_id is None:
            # Сохраняем кластер с большим размером
            query = """
                SELECT id,
                       (SELECT COUNT(*) FROM trend_cluster_posts WHERE cluster_id = trend_clusters.id) as size
                FROM trend_clusters
                WHERE id IN ($1, $2)
                ORDER BY size DESC
                LIMIT 1;
            """
            async with self.db_pool.acquire() as conn:
                record = await conn.fetchrow(query, cluster1_uuid, cluster2_uuid)

            if not record:
                return {
                    "success": False,
                    "merged_cluster_id": None,
                    "reasoning": "Clusters not found",
                }

            keep_cluster_uuid = record.get("id")
            remove_cluster_uuid = cluster2_uuid if keep_cluster_uuid == cluster1_uuid else cluster1_uuid
        else:
            try:
                keep_cluster_uuid = uuid.UUID(keep_cluster_id)
                remove_cluster_uuid = cluster2_uuid if keep_cluster_uuid == cluster1_uuid else cluster1_uuid
            except (ValueError, TypeError):
                return {
                    "success": False,
                    "merged_cluster_id": None,
                    "reasoning": "Invalid keep_cluster_id",
                }

        try:
            async with self.db_pool.acquire() as conn:
                async with conn.transaction():
                    # Перемещаем посты из удаляемого кластера в сохраняемый
                    update_query = """
                        UPDATE trend_cluster_posts
                        SET cluster_id = $1
                        WHERE cluster_id = $2;
                    """
                    await conn.execute(update_query, keep_cluster_uuid, remove_cluster_uuid)

                    # Получаем метаданные обоих кластеров для объединения
                    get_clusters_query = """
                        SELECT id, keywords, primary_topic, summary, topics
                        FROM trend_clusters
                        WHERE id IN ($1, $2);
                    """
                    records = await conn.fetch(get_clusters_query, keep_cluster_uuid, remove_cluster_uuid)

                    if len(records) != 2:
                        raise ValueError("Not all clusters found")

                    # Объединяем keywords и topics
                    merged_keywords = set()
                    merged_topics = set()
                    merged_summary = None

                    for record in records:
                        keywords = record.get("keywords") or []
                        if isinstance(keywords, list):
                            merged_keywords.update(keywords[:10])
                        topics = record.get("topics") or []
                        if isinstance(topics, list):
                            merged_topics.update(topics[:5])

                        if not merged_summary and record.get("summary"):
                            merged_summary = record.get("summary")

                    # Обновляем сохраняемый кластер
                    update_cluster_query = """
                        UPDATE trend_clusters
                        SET keywords = $1::jsonb,
                            topics = $2::jsonb,
                            summary = COALESCE($3, summary),
                            last_activity_at = NOW()
                        WHERE id = $4;
                    """
                    await conn.execute(
                        update_cluster_query,
                        json.dumps(list(merged_keywords)[:20]),
                        json.dumps(list(merged_topics)[:10]),
                        merged_summary,
                        keep_cluster_uuid,
                    )

                    # Помечаем удаляемый кластер как archived
                    archive_query = """
                        UPDATE trend_clusters
                        SET status = 'archived'
                        WHERE id = $1;
                    """
                    await conn.execute(archive_query, remove_cluster_uuid)

            # Обновляем Qdrant: удаляем вектор удаляемого кластера
            if self.qdrant_client:
                try:
                    remove_cluster_key = await self._get_cluster_key(str(remove_cluster_uuid))
                    if remove_cluster_key:
                        collection_name = os.getenv("TRENDS_HOT_COLLECTION", "trends_hot")
                        await self.qdrant_client.delete_vectors(
                            collection_name=collection_name,
                            vector_ids=[remove_cluster_key],
                        )
                except Exception as exc:
                    logger.debug(
                        "merge_agent_qdrant_cleanup_failed",
                        error=str(exc),
                        cluster_id=str(remove_cluster_uuid),
                    )

            return {
                "success": True,
                "merged_cluster_id": str(keep_cluster_uuid),
                "reasoning": f"Merged {remove_cluster_uuid} into {keep_cluster_uuid}",
            }

        except Exception as exc:
            logger.error(
                "merge_agent_apply_failed",
                error=str(exc),
                cluster1_id=cluster1_id,
                cluster2_id=cluster2_id,
            )
            return {
                "success": False,
                "merged_cluster_id": None,
                "reasoning": f"Failed to merge clusters: {str(exc)}",
            }

    async def _calculate_keyword_overlap(
        self,
        keywords1: List[str],
        keywords2: List[str],
    ) -> float:
        """Вычислить пересечение ключевых слов между двумя кластерами."""
        if not keywords1 or not keywords2:
            return 0.0

        # Нормализуем ключевые слова
        set1 = {kw.lower().strip() for kw in keywords1 if kw}
        set2 = {kw.lower().strip() for kw in keywords2 if kw}

        if not set1 or not set2:
            return 0.0

        # Jaccard similarity
        intersection = len(set1 & set2)
        union = len(set1 | set2)

        if union == 0:
            return 0.0

        return float(intersection / union)

    async def _calculate_centroid_similarity(
        self,
        embedding1: Optional[List[float]],
        embedding2: Optional[List[float]],
    ) -> float:
        """Вычислить косинусное сходство между центроидами кластеров."""
        if not embedding1 or not embedding2:
            return 0.0

        if len(embedding1) != len(embedding2):
            return 0.0

        try:
            vec1_array = np.array(embedding1)
            vec2_array = np.array(embedding2)

            norm1 = np.linalg.norm(vec1_array)
            norm2 = np.linalg.norm(vec2_array)

            if norm1 == 0 or norm2 == 0:
                return 0.0

            similarity = np.dot(vec1_array, vec2_array) / (norm1 * norm2)
            return float(max(0.0, min(1.0, similarity)))

        except Exception:
            return 0.0

    def _parse_cluster_record(self, record: asyncpg.Record) -> Dict[str, Any]:
        """Распарсить запись кластера из БД."""
        keywords_raw = record.get("keywords")
        if isinstance(keywords_raw, list):
            keywords = keywords_raw
        elif isinstance(keywords_raw, str):
            try:
                keywords = json.loads(keywords_raw)
            except (json.JSONDecodeError, TypeError):
                keywords = []
        else:
            keywords = []

        embedding_raw = record.get("trend_embedding")
        embedding = None
        if embedding_raw:
            if isinstance(embedding_raw, list):
                embedding = embedding_raw
            elif hasattr(embedding_raw, "tolist"):
                embedding = embedding_raw.tolist()

        return {
            "id": str(record.get("id")),
            "cluster_key": record.get("cluster_key"),
            "keywords": keywords or [],
            "primary_topic": record.get("primary_topic"),
            "embedding": embedding,
            "size": record.get("size") or 0,
        }

    async def _get_cluster_info(self, cluster_id: str) -> Optional[Dict[str, Any]]:
        """Получить информацию о кластере."""
        if not self.db_pool:
            return None

        try:
            cluster_uuid = uuid.UUID(cluster_id)
        except (ValueError, TypeError):
            return None

        query = """
            SELECT id, cluster_key, label, summary, keywords, primary_topic, topics
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

        topics = record.get("topics")
        if isinstance(topics, str):
            try:
                topics = json.loads(topics)
            except (json.JSONDecodeError, TypeError):
                topics = []

        return {
            "id": str(record.get("id")),
            "cluster_key": record.get("cluster_key"),
            "label": record.get("label"),
            "summary": record.get("summary"),
            "keywords": keywords or [],
            "primary_topic": record.get("primary_topic"),
            "topics": topics or [],
        }

    async def _get_cluster_key(self, cluster_id: str) -> Optional[str]:
        """Получить cluster_key кластера."""
        if not self.db_pool:
            return None

        try:
            cluster_uuid = uuid.UUID(cluster_id)
        except (ValueError, TypeError):
            return None

        query = """
            SELECT cluster_key
            FROM trend_clusters
            WHERE id = $1;
        """
        async with self.db_pool.acquire() as conn:
            cluster_key = await conn.fetchval(query, cluster_uuid)

        return cluster_key

    async def _call_merge_validation_llm(
        self,
        cluster1_info: Dict[str, Any],
        cluster2_info: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Вызвать LLM для валидации слияния кластеров."""
        api_base = (
            getattr(settings, "openai_api_base", None)
            or os.getenv("OPENAI_API_BASE")
            or os.getenv("GIGACHAT_PROXY_URL")
            or "http://gpt2giga-proxy:8090"
        )

        system_message = (
            "Ты — агент валидации слияния кластеров трендов. "
            "Оцени, правильно ли объединить два кластера в один. "
            "Кластеры должны быть про одну тему или очень близкие темы. "
            "Верни JSON с полями 'validated' (true/false) и 'reasoning' (объяснение)."
        )

        user_message = (
            f"Кластер 1: {cluster1_info.get('label') or cluster1_info.get('primary_topic')}\n"
            f"Описание: {cluster1_info.get('summary', '')[:200]}\n"
            f"Ключевые слова: {', '.join(cluster1_info.get('keywords', [])[:10])}\n\n"
            f"Кластер 2: {cluster2_info.get('label') or cluster2_info.get('primary_topic')}\n"
            f"Описание: {cluster2_info.get('summary', '')[:200]}\n"
            f"Ключевые слова: {', '.join(cluster2_info.get('keywords', [])[:10])}\n\n"
            "Можно ли объединить эти кластеры? Они про одну тему?"
        )

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{api_base}/v1/chat/completions",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', 'dummy')}",
                    },
                    json={
                        "model": os.getenv("TREND_MERGE_LLM_MODEL", "GigaChat"),
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
                    validated = "true" in content.lower() or "valid" in content.lower() or "да" in content.lower()
                    result = {
                        "validated": validated,
                        "reasoning": content[:200],
                    }

                return result

        except Exception as exc:
            logger.debug("merge_agent_llm_call_failed", error=str(exc))
            raise


def create_merge_agent(
    db_pool: Optional[asyncpg.Pool] = None,
    qdrant_client: Optional[QdrantClient] = None,
    keyword_extractor: Optional[TrendKeywordExtractor] = None,
) -> TrendMergeAgent:
    """Создание экземпляра TrendMergeAgent."""
    return TrendMergeAgent(
        db_pool=db_pool,
        qdrant_client=qdrant_client,
        keyword_extractor=keyword_extractor,
    )

