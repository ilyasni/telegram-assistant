"""
Trend Domain - доменный модуль для детекции трендов.
"""

from __future__ import annotations

from typing import Dict, Any, Optional
from uuid import UUID

import structlog

from api.worker.domains.base_domain import DomainModule
from api.worker.services.retrieval_service import RetrievalService

logger = structlog.get_logger(__name__)


class TrendDomain(DomainModule):
    """Доменный модуль для детекции трендов."""
    
    def __init__(self):
        super().__init__(
            domain_name="trend",
            max_docs_per_domain=50,  # Больше документов для трендов
            max_graph_depth=3,
        )
        self._quality_threshold = 0.7
    
    def get_prompt_templates(self) -> Dict[str, Any]:
        """Получить промпты для trend домена."""
        # TODO: Импортировать промпты из trends_taxonomy_agent
        return {}
    
    async def process(
        self,
        tenant_id: UUID | str,
        query: Dict[str, Any],
        retrieval_service: Optional[RetrievalService] = None,
    ) -> Dict[str, Any]:
        """
        Обработать запрос на тренды.
        
        Performance: Использует общий retrieval_service для получения документов.
        TODO: Полная интеграция с существующим trend detection pipeline.
        """
        # Если передан retrieval_service, используем его для получения документов
        if retrieval_service:
            try:
                query_text = query.get("query", "") or query.get("text", "")
                retrieval_result = await retrieval_service.retrieve(
                    tenant_id=str(tenant_id),
                    query_text=query_text,
                    domain="trend",
                    limit_docs=self.max_docs_per_domain,
                    max_docs_per_domain=self.max_docs_per_domain,
                    max_graph_depth=self.max_graph_depth,
                )
                
                return {
                    "domain": "trend",
                    "status": "processed",
                    "documents_count": len(retrieval_result.documents),
                    "graph_entities_count": len(retrieval_result.graph_entities),
                    "cache_hit": retrieval_result.cache_hit,
                }
            except Exception as e:
                logger.warning("trend_domain.retrieval_failed", error=str(e))
        
        # Placeholder для будущей полной реализации
        return {
            "domain": "trend",
            "status": "partial",
            "message": "Trend domain processing partially implemented (retrieval only)",
        }

