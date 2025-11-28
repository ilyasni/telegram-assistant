"""
Drift Detector Agent для обнаружения дрейфа темы кластера.

Context7: Проверяет изменение центроида кластера при добавлении новых постов
для обнаружения смешивания разных тем в один кластер.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List, Optional

import asyncpg
import numpy as np
import structlog

logger = structlog.get_logger()


# ============================================================================
# DRIFT DETECTOR AGENT
# ============================================================================


class DriftDetectorAgent:
    """
    Агент обнаружения дрейфа темы кластера:
    - пересчитывает центроид кластера после добавления поста,
    - сравнивает старый и новый центроид,
    - определяет, произошел ли дрейф темы.
    """

    def __init__(self, db_pool: Optional[asyncpg.Pool] = None):
        """
        Инициализация DriftDetectorAgent.
        
        Args:
            db_pool: Пул подключений к БД для получения embedding кластера
        """
        self.db_pool = db_pool
        self.drift_threshold = float(os.getenv("TREND_DRIFT_THRESHOLD", "0.05"))

    async def detect_drift(
        self, cluster_id: str, new_post_embedding: List[float]
    ) -> Dict[str, Any]:
        """
        Обнаружение дрейфа темы кластера.
        
        Args:
            cluster_id: ID кластера
            new_post_embedding: Embedding нового поста
        
        Returns:
            Dict с полями:
            - drift_detected: bool - обнаружен ли дрейф
            - delta: float - изменение центроида (0.0-1.0)
            - old_centroid: List[float] - старый центроид (если доступен)
            - new_centroid: List[float] - новый центроид
            - reasoning: str - объяснение результата
        """
        if not self.db_pool or not new_post_embedding:
            return {
                "drift_detected": False,
                "delta": 0.0,
                "reasoning": "DB pool or embedding unavailable",
            }

        try:
            cluster_uuid = uuid.UUID(cluster_id)
        except (ValueError, TypeError):
            return {
                "drift_detected": False,
                "delta": 0.0,
                "reasoning": "Invalid cluster_id",
            }

        try:
            # Получаем текущий центроид кластера (trend_embedding)
            old_centroid = await self._get_cluster_centroid(cluster_id)
            if not old_centroid:
                # Если центроида нет, это новый кластер - дрейфа нет
                return {
                    "drift_detected": False,
                    "delta": 0.0,
                    "reasoning": "New cluster, no centroid yet",
                }

            # Получаем все embedding постов кластера для пересчета центроида
            cluster_embeddings = await self._get_cluster_embeddings(cluster_id)
            if not cluster_embeddings:
                return {
                    "drift_detected": False,
                    "delta": 0.0,
                    "reasoning": "No embeddings in cluster",
                }

            # Добавляем новый embedding к списку
            cluster_embeddings.append(new_post_embedding)

            # Пересчитываем центроид как среднее всех embedding
            new_centroid = self._calculate_centroid(cluster_embeddings)

            # Сравниваем старый и новый центроид (cosine similarity)
            delta = self._cosine_distance(old_centroid, new_centroid)

            drift_detected = delta > self.drift_threshold

            reasoning = (
                f"Drift detected: delta={delta:.4f} > threshold={self.drift_threshold}"
                if drift_detected
                else f"No drift: delta={delta:.4f} <= threshold={self.drift_threshold}"
            )

            return {
                "drift_detected": drift_detected,
                "delta": delta,
                "old_centroid": old_centroid,
                "new_centroid": new_centroid,
                "reasoning": reasoning,
            }
        except Exception as exc:
            logger.error(
                "drift_detector_detection_failed",
                error=str(exc),
                cluster_id=cluster_id,
            )
            return {
                "drift_detected": False,
                "delta": 0.0,
                "reasoning": f"Error in drift detection: {str(exc)}",
            }

    async def _get_cluster_centroid(self, cluster_id: str) -> Optional[List[float]]:
        """Получить текущий центроид кластера (trend_embedding)."""
        if not self.db_pool:
            return None
        try:
            cluster_uuid = uuid.UUID(cluster_id)
        except (ValueError, TypeError):
            return None
        query = """
            SELECT trend_embedding
            FROM trend_clusters
            WHERE id = $1 AND trend_embedding IS NOT NULL;
        """
        async with self.db_pool.acquire() as conn:
            embedding = await conn.fetchval(query, cluster_uuid)
        if embedding:
            # Преобразуем из PostgreSQL array в Python list
            if isinstance(embedding, list):
                return embedding
            elif hasattr(embedding, "tolist"):
                return embedding.tolist()
        return None

    async def _get_cluster_embeddings(self, cluster_id: str) -> List[List[float]]:
        """Получить все embedding постов кластера."""
        if not self.db_pool:
            return []
        try:
            cluster_uuid = uuid.UUID(cluster_id)
        except (ValueError, TypeError):
            return []
        # Получаем embedding из Qdrant или из связанных постов через БД
        # Пока упрощенная версия - получаем только текущий trend_embedding
        # В будущем можно расширить для получения всех embedding постов
        query = """
            SELECT trend_embedding
            FROM trend_clusters
            WHERE id = $1 AND trend_embedding IS NOT NULL;
        """
        async with self.db_pool.acquire() as conn:
            embedding = await conn.fetchval(query, cluster_uuid)
        if embedding:
            if isinstance(embedding, list):
                return [embedding]
            elif hasattr(embedding, "tolist"):
                return [embedding.tolist()]
        return []

    def _calculate_centroid(self, embeddings: List[List[float]]) -> List[float]:
        """Рассчитать центроид как среднее всех embedding."""
        if not embeddings:
            return []
        # Преобразуем в numpy array для удобства вычислений
        embeddings_array = np.array(embeddings)
        centroid = np.mean(embeddings_array, axis=0)
        return centroid.tolist()

    def _cosine_distance(
        self, vec1: List[float], vec2: List[float]
    ) -> float:
        """
        Вычислить косинусное расстояние между двумя векторами.
        
        Returns:
            Расстояние в диапазоне [0.0, 2.0], где 0.0 - векторы идентичны,
            2.0 - векторы противоположны.
        """
        if not vec1 or not vec2 or len(vec1) != len(vec2):
            return 1.0  # Максимальное расстояние при несоответствии размеров

        vec1_array = np.array(vec1)
        vec2_array = np.array(vec2)

        # Нормализуем векторы
        norm1 = np.linalg.norm(vec1_array)
        norm2 = np.linalg.norm(vec2_array)

        if norm1 == 0 or norm2 == 0:
            return 1.0

        vec1_normalized = vec1_array / norm1
        vec2_normalized = vec2_array / norm2

        # Косинусное сходство
        cosine_similarity = np.dot(vec1_normalized, vec2_normalized)

        # Преобразуем в расстояние: 1.0 - similarity
        # Но так как similarity может быть отрицательной, используем abs
        distance = 1.0 - abs(cosine_similarity)

        return float(distance)

