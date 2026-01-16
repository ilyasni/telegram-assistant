"""
Trend Coherence Metrics для оценки качества кластеризации.

Context7: Вычисляет формальные метрики когерентности кластеров:
- Topic Coherence (NPMI): семантическая связанность топ-слов
- Silhouette Score: мера разделимости кластеров
- Intra-cluster Cosine Similarity: средняя косинусная близость внутри кластера
- Keyword Overlap: пересечение ключевых слов между кластерами
"""

from __future__ import annotations

import math
import uuid
from collections import Counter
from typing import Any, Dict, List, Optional, Set, Tuple

import asyncpg
import numpy as np
from sklearn.metrics import silhouette_score
import structlog

logger = structlog.get_logger()


# ============================================================================
# COHERENCE METRICS CALCULATOR
# ============================================================================


class TrendCoherenceMetrics:
    """
    Калькулятор метрик когерентности для кластеров трендов.
    
    Вычисляет:
    - Intra-cluster similarity: средняя косинусная близость постов внутри кластера
    - Topic Coherence (NPMI): нормализованная взаимная информация между топ-словами
    - Silhouette Score: мера разделимости кластера от других
    - Keyword Overlap: пересечение ключевых слов между кластерами
    """

    def __init__(self, db_pool: Optional[asyncpg.Pool] = None):
        """
        Инициализация калькулятора метрик.
        
        Args:
            db_pool: Пул подключений к БД для получения данных кластеров
        """
        self.db_pool = db_pool

    async def calculate_intra_cluster_similarity(
        self,
        cluster_id: str,
        embeddings: List[List[float]],
    ) -> Optional[float]:
        """
        Вычислить среднюю косинусную близость постов внутри кластера.
        
        Args:
            cluster_id: ID кластера
            embeddings: Список embedding постов кластера
        
        Returns:
            Средняя косинусная близость (0.0-1.0) или None при недостатке данных
        """
        if not embeddings or len(embeddings) < 2:
            return None

        try:
            embeddings_array = np.array(embeddings)
            similarities = []

            # Вычисляем косинусную близость между всеми парами постов
            for i in range(len(embeddings)):
                for j in range(i + 1, len(embeddings)):
                    similarity = self._cosine_similarity(
                        embeddings[i], embeddings[j]
                    )
                    if similarity is not None:
                        similarities.append(similarity)

            if not similarities:
                return None

            avg_similarity = float(np.mean(similarities))
            return max(0.0, min(1.0, avg_similarity))

        except Exception as exc:
            logger.debug(
                "coherence_metrics_intra_cluster_failed",
                error=str(exc),
                cluster_id=cluster_id,
            )
            return None

    async def calculate_keyword_overlap(
        self,
        cluster_id_1: str,
        keywords_1: List[str],
        cluster_id_2: str,
        keywords_2: List[str],
    ) -> float:
        """
        Вычислить пересечение ключевых слов между двумя кластерами.
        
        Args:
            cluster_id_1: ID первого кластера
            keywords_1: Ключевые слова первого кластера
            cluster_id_2: ID второго кластера
            keywords_2: Ключевые слова второго кластера
        
        Returns:
            Коэффициент пересечения (0.0-1.0), где 1.0 = полное пересечение
        """
        if not keywords_1 or not keywords_2:
            return 0.0

        # Нормализуем ключевые слова (lowercase, убираем дубликаты)
        set1 = {kw.lower().strip() for kw in keywords_1 if kw}
        set2 = {kw.lower().strip() for kw in keywords_2 if kw}

        if not set1 or not set2:
            return 0.0

        # Jaccard similarity (пересечение / объединение)
        intersection = len(set1 & set2)
        union = len(set1 | set2)

        if union == 0:
            return 0.0

        return float(intersection / union)

    async def calculate_npmi_coherence(
        self,
        cluster_id: str,
        top_keywords: List[str],
        all_cluster_keywords: List[List[str]],
    ) -> Optional[float]:
        """
        Вычислить Topic Coherence через нормализованную взаимную информацию (NPMI).
        
        Упрощённая версия NPMI для оценки связанности топ-слов кластера.
        Использует частоту совместного появления слов в постах кластера.
        
        Args:
            cluster_id: ID кластера
            top_keywords: Топ-N ключевых слов кластера (обычно 5-10)
            all_cluster_keywords: Список списков ключевых слов для всех постов кластера
        
        Returns:
            NPMI score (-1.0 до 1.0), где 1.0 = высокая связанность, или None
        """
        if not top_keywords or len(top_keywords) < 2:
            return None
        if not all_cluster_keywords:
            return None

        try:
            # Нормализуем ключевые слова
            top_keywords_normalized = [kw.lower().strip() for kw in top_keywords if kw]
            if len(top_keywords_normalized) < 2:
                return None

            # Строим частотную матрицу совместного появления
            # Считаем, в скольких постах встречаются пары слов
            num_posts = len(all_cluster_keywords)
            if num_posts < 2:
                return None

            # Нормализуем ключевые слова в каждом посте
            normalized_posts = [
                {kw.lower().strip() for kw in post_keywords if kw}
                for post_keywords in all_cluster_keywords
            ]

            # Вычисляем PMI для всех пар топ-слов
            pmi_scores = []
            for i, word1 in enumerate(top_keywords_normalized[:10]):  # Ограничиваем для производительности
                for word2 in top_keywords_normalized[i + 1 : i + 1 + 5]:  # До 5 пар для каждого слова
                    if word1 == word2:
                        continue

                    # Считаем частоты
                    count_word1 = sum(1 for post in normalized_posts if word1 in post)
                    count_word2 = sum(1 for post in normalized_posts if word2 in post)
                    count_both = sum(1 for post in normalized_posts if word1 in post and word2 in post)

                    if count_both == 0:
                        continue  # Пропускаем пары, которые никогда не встречаются вместе

                    # Вычисляем PMI
                    p_word1 = count_word1 / num_posts
                    p_word2 = count_word2 / num_posts
                    p_both = count_both / num_posts

                    if p_word1 == 0 or p_word2 == 0 or p_both == 0:
                        continue

                    pmi = math.log2(p_both / (p_word1 * p_word2))

                    # Нормализуем до NPMI: PMI / -log(P(w1, w2))
                    # Context7: Защита от division by zero
                    denom = -math.log2(p_both) if p_both > 0 else 1.0
                    if abs(denom) < 1e-10:  # Избегаем деления на очень маленькое число
                        continue
                    npmi = pmi / denom
                    pmi_scores.append(npmi)

            if not pmi_scores:
                return None

            # Возвращаем средний NPMI
            avg_npmi = float(np.mean(pmi_scores))
            return max(-1.0, min(1.0, avg_npmi))

        except Exception as exc:
            logger.debug(
                "coherence_metrics_npmi_failed",
                error=str(exc),
                cluster_id=cluster_id,
            )
            return None

    async def calculate_silhouette_score(
        self,
        cluster_id: str,
        cluster_embeddings: List[List[float]],
        all_clusters_embeddings: Optional[Dict[str, List[List[float]]]] = None,
    ) -> Optional[float]:
        """
        Вычислить silhouette score для кластера.
        
        Silhouette score показывает, насколько хорошо кластер отделён от других.
        Значение в диапазоне [-1, 1], где:
        - 1: кластер хорошо отделён
        - 0: кластер на границе
        - -1: кластер неправильно определён
        
        Args:
            cluster_id: ID кластера
            cluster_embeddings: Embedding постов кластера
            all_clusters_embeddings: Dict[cluster_id, embeddings] для всех кластеров
        
        Returns:
            Silhouette score (-1.0 до 1.0) или None
        """
        if not cluster_embeddings or len(cluster_embeddings) < 2:
            return None

        try:
            if all_clusters_embeddings is None:
                # Если другие кластеры не предоставлены, используем только текущий
                # В этом случае silhouette не имеет смысла, возвращаем None
                return None

            # Строим матрицу всех embeddings и метки кластеров
            all_embeddings = []
            labels = []

            cluster_idx = 0
            for cid, embeddings in all_clusters_embeddings.items():
                for emb in embeddings:
                    all_embeddings.append(emb)
                    labels.append(cluster_idx)
                if cid != cluster_id:
                    cluster_idx += 1

            # Добавляем текущий кластер последним
            for emb in cluster_embeddings:
                all_embeddings.append(emb)
                labels.append(cluster_idx)

            if len(set(labels)) < 2:
                return None  # Нужно минимум 2 кластера для silhouette

            all_embeddings_array = np.array(all_embeddings)

            # Вычисляем silhouette score
            score = silhouette_score(all_embeddings_array, labels, metric="cosine")
            return float(score)

        except Exception as exc:
            logger.debug(
                "coherence_metrics_silhouette_failed",
                error=str(exc),
                cluster_id=cluster_id,
            )
            return None

    async def get_cluster_keywords(
        self, cluster_id: str
    ) -> Tuple[List[str], List[List[str]]]:
        """
        Получить ключевые слова кластера и всех его постов.
        
        Args:
            cluster_id: ID кластера
        
        Returns:
            Tuple[top_keywords, all_post_keywords], где:
            - top_keywords: список ключевых слов кластера из БД
            - all_post_keywords: список списков ключевых слов для каждого поста
        """
        if not self.db_pool:
            return [], []

        try:
            cluster_uuid = uuid.UUID(cluster_id)
        except (ValueError, TypeError):
            return [], []

        # Получаем ключевые слова кластера
        cluster_query = """
            SELECT keywords
            FROM trend_clusters
            WHERE id = $1;
        """
        async with self.db_pool.acquire() as conn:
            cluster_record = await conn.fetchrow(cluster_query, cluster_uuid)

        top_keywords = []
        if cluster_record:
            keywords_raw = cluster_record.get("keywords")
            if isinstance(keywords_raw, list):
                top_keywords = keywords_raw
            elif isinstance(keywords_raw, str):
                import json
                try:
                    top_keywords = json.loads(keywords_raw)
                except (json.JSONDecodeError, TypeError):
                    top_keywords = []

        # Получаем ключевые слова всех постов кластера
        posts_query = """
            SELECT COALESCE(pe.data->'keywords', '[]'::jsonb) AS keywords
            FROM trend_cluster_posts tcp
            JOIN posts p ON p.id = tcp.post_id
            LEFT JOIN post_enrichment pe ON pe.post_id = p.id AND pe.kind = 'classify'
            WHERE tcp.cluster_id = $1
            LIMIT 100;  -- Ограничиваем для производительности
        """
        all_post_keywords = []
        async with self.db_pool.acquire() as conn:
            post_records = await conn.fetch(posts_query, cluster_uuid)

        for record in post_records:
            keywords_raw = record.get("keywords")
            if isinstance(keywords_raw, list):
                all_post_keywords.append(keywords_raw)
            elif isinstance(keywords_raw, str):
                import json
                try:
                    keywords = json.loads(keywords_raw)
                    all_post_keywords.append(keywords)
                except (json.JSONDecodeError, TypeError):
                    pass

        return top_keywords, all_post_keywords

    async def calculate_all_metrics(
        self,
        cluster_id: str,
        cluster_embeddings: List[List[float]],
        cluster_keywords: Optional[List[str]] = None,
        all_clusters_embeddings: Optional[Dict[str, List[List[float]]]] = None,
    ) -> Dict[str, Any]:
        """
        Вычислить все метрики когерентности для кластера.
        
        Args:
            cluster_id: ID кластера
            cluster_embeddings: Embedding постов кластера
            cluster_keywords: Ключевые слова кластера (опционально)
            all_clusters_embeddings: Embedding других кластеров для silhouette
        
        Returns:
            Dict с метриками:
            - intra_cluster_similarity: float | None
            - npmi_score: float | None
            - silhouette_score: float | None
            - keyword_count: int
        """
        result = {
            "intra_cluster_similarity": None,
            "npmi_score": None,
            "silhouette_score": None,
            "keyword_count": 0,
        }

        # Intra-cluster similarity
        intra_similarity = await self.calculate_intra_cluster_similarity(
            cluster_id, cluster_embeddings
        )
        result["intra_cluster_similarity"] = intra_similarity

        # NPMI Coherence (нужны ключевые слова)
        if cluster_keywords is None:
            top_keywords, all_post_keywords = await self.get_cluster_keywords(cluster_id)
        else:
            top_keywords = cluster_keywords
            _, all_post_keywords = await self.get_cluster_keywords(cluster_id)

        result["keyword_count"] = len(top_keywords)

        if top_keywords and all_post_keywords:
            npmi_score = await self.calculate_npmi_coherence(
                cluster_id, top_keywords, all_post_keywords
            )
            result["npmi_score"] = npmi_score

        # Silhouette Score (если предоставлены другие кластеры)
        if all_clusters_embeddings:
            silhouette = await self.calculate_silhouette_score(
                cluster_id, cluster_embeddings, all_clusters_embeddings
            )
            result["silhouette_score"] = silhouette

        return result

    def _cosine_similarity(
        self, vec1: List[float], vec2: List[float]
    ) -> Optional[float]:
        """Вычислить косинусную близость между двумя векторами."""
        if not vec1 or not vec2 or len(vec1) != len(vec2):
            return None

        try:
            vec1_array = np.array(vec1)
            vec2_array = np.array(vec2)

            norm1 = np.linalg.norm(vec1_array)
            norm2 = np.linalg.norm(vec2_array)

            if norm1 == 0 or norm2 == 0:
                return 0.0

            similarity = np.dot(vec1_array, vec2_array) / (norm1 * norm2)
            return float(similarity)

        except Exception:
            return None


def create_coherence_metrics(
    db_pool: Optional[asyncpg.Pool] = None,
) -> TrendCoherenceMetrics:
    """Создание экземпляра TrendCoherenceMetrics."""
    return TrendCoherenceMetrics(db_pool=db_pool)

