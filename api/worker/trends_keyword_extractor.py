"""
Trend Keyword Extractor с c-TF-IDF для взвешивания ключевых слов кластеров.

Context7: Реализует class-based TF-IDF (как в BERTopic) для интерпретации тем кластеров.
c-TF-IDF вычисляет TF-IDF на уровне кластера, а не документа, что позволяет
получить более релевантные ключевые слова для описания темы.
"""

from __future__ import annotations

import math
import uuid
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import structlog

logger = structlog.get_logger()


# ============================================================================
# C-TF-IDF KEYWORD EXTRACTOR
# ============================================================================


class TrendKeywordExtractor:
    """
    Экстрактор ключевых слов с использованием c-TF-IDF.
    
    Class-based TF-IDF:
    - c-TF: частота слова в кластере / общее количество слов в кластере
    - IDF: log(общее количество кластеров / количество кластеров, содержащих слово)
    - c-TF-IDF = c-TF * IDF
    
    Это позволяет получить взвешенные ключевые слова, которые лучше
    описывают тему кластера по сравнению с обычным TF-IDF.
    """

    def __init__(self, db_pool: Optional[asyncpg.Pool] = None):
        """
        Инициализация экстрактора ключевых слов.
        
        Args:
            db_pool: Пул подключений к БД для получения данных постов
        """
        self.db_pool = db_pool

    async def compute_ctfidf_keywords(
        self,
        cluster_id: str,
        cluster_posts_content: Optional[List[str]] = None,
        top_n: int = 10,
    ) -> List[Tuple[str, float]]:
        """
        Вычислить c-TF-IDF ключевые слова для кластера.
        
        Args:
            cluster_id: ID кластера
            cluster_posts_content: Список текстов постов кластера (опционально)
            top_n: Количество топ-ключевых слов для возврата
        
        Returns:
            Список кортежей (keyword, ctfidf_score) отсортированный по убыванию score
        """
        if not self.db_pool:
            return []

        try:
            cluster_uuid = uuid.UUID(cluster_id)
        except (ValueError, TypeError):
            return []

        # Получаем тексты постов кластера, если не предоставлены
        if cluster_posts_content is None:
            cluster_posts_content = await self._get_cluster_posts_content(cluster_id)

        if not cluster_posts_content:
            return []

        # Токенизируем и нормализуем тексты постов
        cluster_words = self._tokenize_and_normalize(cluster_posts_content)

        if not cluster_words:
            return []

        # Вычисляем c-TF для кластера
        cluster_word_freq = Counter(cluster_words)
        total_words_in_cluster = len(cluster_words)
        ctf_scores = {
            word: freq / total_words_in_cluster
            for word, freq in cluster_word_freq.items()
        }

        # Получаем общее количество кластеров и частоту слова в кластерах для IDF
        num_total_clusters = await self._get_total_clusters_count()

        if num_total_clusters == 0:
            return []

        # Вычисляем IDF для каждого слова
        ctfidf_scores = {}
        for word in ctf_scores.keys():
            # Считаем, в скольких кластерах встречается это слово
            clusters_with_word = await self._count_clusters_with_word(word)
            
            if clusters_with_word == 0:
                # Если слово не встречается в других кластерах, IDF = 0 (не интересно)
                continue

            idf = math.log(num_total_clusters / clusters_with_word)
            ctfidf_scores[word] = ctf_scores[word] * idf

        # Сортируем по убыванию c-TF-IDF и возвращаем топ-N
        sorted_keywords = sorted(
            ctfidf_scores.items(), key=lambda x: x[1], reverse=True
        )

        return sorted_keywords[:top_n]

    async def compute_ctfidf_keywords_simple(
        self,
        cluster_keywords: List[str],
        cluster_posts_keywords: List[List[str]],
        all_clusters_keywords: Optional[List[List[str]]] = None,
        top_n: int = 10,
    ) -> List[Tuple[str, float]]:
        """
        Упрощённая версия c-TF-IDF на основе уже извлечённых ключевых слов.
        
        Используется, когда у нас уже есть keywords постов и кластеров,
        без необходимости токенизации всего текста.
        
        Args:
            cluster_keywords: Текущие ключевые слова кластера
            cluster_posts_keywords: Список списков keywords для каждого поста кластера
            all_clusters_keywords: Список списков keywords всех кластеров (для IDF)
            top_n: Количество топ-ключевых слов
        
        Returns:
            Список кортежей (keyword, ctfidf_score)
        """
        if not cluster_posts_keywords:
            return []

        # Объединяем все keywords постов кластера
        all_cluster_words = []
        for post_keywords in cluster_posts_keywords:
            if post_keywords:
                # Нормализуем keywords
                normalized = [kw.lower().strip() for kw in post_keywords if kw]
                all_cluster_words.extend(normalized)

        if not all_cluster_words:
            return []

        # Вычисляем c-TF
        cluster_word_freq = Counter(all_cluster_words)
        total_words_in_cluster = len(all_cluster_words)
        ctf_scores = {
            word: freq / total_words_in_cluster
            for word, freq in cluster_word_freq.items()
        }

        # Вычисляем IDF
        if all_clusters_keywords is None:
            # Если не предоставлены все кластеры, используем упрощённый IDF
            # (считаем, что слово встречается в небольшом проценте кластеров)
            idf_scores = {
                word: math.log(10.0)  # Фиксированный IDF
                for word in ctf_scores.keys()
            }
        else:
            # Считаем частоту слова в кластерах
            num_total_clusters = len(all_clusters_keywords)
            if num_total_clusters == 0:
                return []

            word_cluster_count = Counter()
            for cluster_keywords_list in all_clusters_keywords:
                normalized_cluster = {
                    kw.lower().strip() for kw in cluster_keywords_list if kw
                }
                word_cluster_count.update(normalized_cluster)

            idf_scores = {}
            for word in ctf_scores.keys():
                clusters_with_word = word_cluster_count.get(word, 1)
                if clusters_with_word == 0:
                    continue
                idf_scores[word] = math.log(num_total_clusters / clusters_with_word)

        # Вычисляем c-TF-IDF
        ctfidf_scores = {
            word: ctf_scores[word] * idf_scores.get(word, 0.0)
            for word in ctf_scores.keys()
            if word in idf_scores
        }

        # Сортируем и возвращаем топ-N
        sorted_keywords = sorted(
            ctfidf_scores.items(), key=lambda x: x[1], reverse=True
        )

        return sorted_keywords[:top_n]

    async def update_cluster_keywords(
        self,
        cluster_id: str,
        ctfidf_keywords: List[Tuple[str, float]],
        merge_with_existing: bool = True,
    ) -> List[str]:
        """
        Обновить keywords кластера на основе c-TF-IDF.
        
        Args:
            cluster_id: ID кластера
            ctfidf_keywords: Список кортежей (keyword, score) из c-TF-IDF
            merge_with_existing: Если True, объединяет с существующими keywords
        
        Returns:
            Обновлённый список keywords
        """
        if not self.db_pool:
            return [kw for kw, _ in ctfidf_keywords]

        try:
            cluster_uuid = uuid.UUID(cluster_id)
        except (ValueError, TypeError):
            return [kw for kw, _ in ctfidf_keywords]

        # Извлекаем только keywords (без scores)
        new_keywords = [kw for kw, _ in ctfidf_keywords]

        if merge_with_existing:
            # Получаем существующие keywords
            query = """
                SELECT keywords
                FROM trend_clusters
                WHERE id = $1;
            """
            async with self.db_pool.acquire() as conn:
                record = await conn.fetchrow(query, cluster_uuid)

            existing_keywords = []
            if record:
                keywords_raw = record.get("keywords")
                if isinstance(keywords_raw, list):
                    existing_keywords = keywords_raw
                elif isinstance(keywords_raw, str):
                    import json
                    try:
                        existing_keywords = json.loads(keywords_raw)
                    except (json.JSONDecodeError, TypeError):
                        pass

            # Объединяем: c-TF-IDF keywords в начале (приоритет), затем существующие
            merged_keywords = []
            seen = set()
            for kw in new_keywords:
                normalized = kw.lower().strip()
                if normalized and normalized not in seen:
                    merged_keywords.append(kw)
                    seen.add(normalized)

            for kw in existing_keywords:
                if isinstance(kw, str):
                    normalized = kw.lower().strip()
                    if normalized and normalized not in seen:
                        merged_keywords.append(kw)
                        seen.add(normalized)

            return merged_keywords[:20]  # Ограничиваем до 20 keywords

        return new_keywords

    async def _get_cluster_posts_content(self, cluster_id: str) -> List[str]:
        """Получить тексты постов кластера."""
        if not self.db_pool:
            return []

        try:
            cluster_uuid = uuid.UUID(cluster_id)
        except (ValueError, TypeError):
            return []

        query = """
            SELECT p.content AS text
            FROM trend_cluster_posts tcp
            JOIN posts p ON p.id = tcp.post_id
            WHERE tcp.cluster_id = $1
            AND p.content IS NOT NULL
            LIMIT 100;  -- Ограничиваем для производительности
        """
        async with self.db_pool.acquire() as conn:
            records = await conn.fetch(query, cluster_uuid)

        return [record.get("text") or "" for record in records if record.get("text")]

    def _tokenize_and_normalize(
        self, texts: List[str], min_word_length: int = 3
    ) -> List[str]:
        """
        Токенизировать и нормализовать тексты.
        
        Args:
            texts: Список текстов
            min_word_length: Минимальная длина слова
        
        Returns:
            Список нормализованных слов (lowercase, без стоп-слов)
        """
        import re

        # Базовые стоп-слова (русский + английский)
        stopwords = {
            "и", "в", "на", "с", "по", "для", "от", "до", "из", "к", "о", "об",
            "the", "a", "an", "and", "or", "of", "in", "on", "to", "for", "with",
        }

        words = []
        for text in texts:
            if not text:
                continue
            # Токенизация: разделяем по пробелам и пунктуации
            tokens = re.findall(r"\b\w+\b", text.lower())
            # Фильтруем стоп-слова и короткие слова
            filtered = [
                token
                for token in tokens
                if token not in stopwords and len(token) >= min_word_length
            ]
            words.extend(filtered)

        return words

    async def _get_total_clusters_count(self) -> int:
        """Получить общее количество кластеров."""
        if not self.db_pool:
            return 0

        query = """
            SELECT COUNT(*) FROM trend_clusters
            WHERE status = 'active';
        """
        async with self.db_pool.acquire() as conn:
            count = await conn.fetchval(query)

        return count or 0

    async def _count_clusters_with_word(self, word: str) -> int:
        """
        Подсчитать количество кластеров, содержащих слово.
        
        Args:
            word: Нормализованное слово
        
        Returns:
            Количество кластеров
        """
        if not self.db_pool:
            return 0

        # Ищем слово в keywords кластера (как JSON массив)
        query = """
            SELECT COUNT(DISTINCT id)
            FROM trend_clusters
            WHERE status = 'active'
            AND (
                keywords::text ILIKE $1
                OR summary ILIKE $1
                OR primary_topic ILIKE $1
            );
        """
        search_pattern = f"%{word}%"

        async with self.db_pool.acquire() as conn:
            count = await conn.fetchval(query, search_pattern)

        return count or 0


def create_keyword_extractor(
    db_pool: Optional[asyncpg.Pool] = None,
) -> TrendKeywordExtractor:
    """Создание экземпляра TrendKeywordExtractor."""
    return TrendKeywordExtractor(db_pool=db_pool)

