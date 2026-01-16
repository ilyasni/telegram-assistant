"""
Graph Cluster Validator для валидации кластеров через Neo4j Graph-RAG.

Context7: Проверяет связность тем через граф знаний для предотвращения
смешивания разных тем в один кластер.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger()


# ============================================================================
# GRAPH CLUSTER VALIDATOR
# ============================================================================


class GraphClusterValidator:
    """
    Валидатор кластеров через граф знаний:
    - проверяет связность тем поста и кластера через Neo4j,
    - использует Graph-RAG для поиска связанных постов,
    - определяет, принадлежит ли пост кластеру на основе графа.
    """

    def __init__(self, graph_service):
        """
        Инициализация GraphClusterValidator.
        
        Args:
            graph_service: Экземпляр GraphService для работы с Neo4j
        """
        self.graph_service = graph_service

    async def validate_cluster_topic(
        self,
        post_topics: List[str],
        post_entities: List[str],
        cluster_id: str,
        cluster_topics: List[str],
    ) -> Dict[str, Any]:
        """
        Валидация связности тем поста и кластера через граф.
        
        Args:
            post_topics: Темы поста
            post_entities: Сущности поста
            cluster_id: ID кластера
            cluster_topics: Темы кластера
        
        Returns:
            Dict с полями:
            - is_disconnected: bool - связаны ли темы через граф
            - similarity_score: float - оценка связности (0.0-1.0)
            - reasoning: str - объяснение результата
        """
        if not self.graph_service:
            return {
                "is_disconnected": False,
                "similarity_score": 1.0,
                "reasoning": "GraphService unavailable",
            }

        try:
            # Проверка доступности Neo4j
            if not await self.graph_service.health_check():
                logger.warning("Neo4j unavailable for graph validation")
                return {
                    "is_disconnected": False,
                    "similarity_score": 1.0,
                    "reasoning": "Neo4j unavailable",
                }

            # Проверяем связность тем через граф
            disconnected = True
            max_similarity = 0.0

            for post_topic in post_topics[:5]:  # Ограничиваем для производительности
                for cluster_topic in cluster_topics[:5]:
                    try:
                        # Поиск связанных тем через граф
                        similar_topics = await self.graph_service.find_similar_topics(
                            post_topic, limit=10
                        )
                        for similar in similar_topics:
                            if similar.get("topic") == cluster_topic:
                                similarity = similar.get("similarity", 0.0)
                                if similarity > max_similarity:
                                    max_similarity = similarity
                                if similarity > 0.7:  # Порог связности
                                    disconnected = False
                                    break
                        if not disconnected:
                            break
                    except Exception as exc:
                        logger.debug(
                            "graph_validator_topic_check_failed",
                            error=str(exc),
                            post_topic=post_topic,
                            cluster_topic=cluster_topic,
                        )
                if not disconnected:
                    break

            # Если не найдено связей через find_similar_topics, пробуем через search_related_posts
            if disconnected and post_topics and cluster_topics:
                try:
                    # Используем первую тему кластера для поиска связанных постов
                    related_posts = await self.graph_service.search_related_posts(
                        query=cluster_topics[0],
                        topic=cluster_topics[0],
                        limit=10,
                    )
                    # Проверяем, есть ли посты с темами, совпадающими с темами поста
                    for related in related_posts:
                        related_topics = related.get("topics", [])
                        for post_topic in post_topics:
                            if post_topic in related_topics:
                                disconnected = False
                                max_similarity = max(max_similarity, 0.6)
                                break
                        if not disconnected:
                            break
                except Exception as exc:
                    logger.debug(
                        "graph_validator_related_posts_failed",
                        error=str(exc),
                        cluster_id=cluster_id,
                    )

            reasoning = (
                "Topics connected via graph"
                if not disconnected
                else f"Topics not connected (max similarity: {max_similarity:.2f})"
            )

            return {
                "is_disconnected": disconnected,
                "similarity_score": max_similarity,
                "reasoning": reasoning,
            }
        except Exception as exc:
            logger.error(
                "graph_validator_validation_failed",
                error=str(exc),
                cluster_id=cluster_id,
            )
            # При ошибке не блокируем добавление поста
            return {
                "is_disconnected": False,
                "similarity_score": 1.0,
                "reasoning": f"Error in validation: {str(exc)}",
            }

