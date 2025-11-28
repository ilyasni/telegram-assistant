"""
Base Domain Module - базовый класс для доменных модулей.

Performance guardrails:
- Общий retrieval слой с батчированием и shared кэшем
- Per-domain ограничения: max_docs_per_domain, max_graph_depth
- Домены не лезут сами в Qdrant/Neo4j, а просят retrieval-layer
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from uuid import UUID

import structlog

logger = structlog.get_logger(__name__)


class DomainModule(ABC):
    """Базовый класс для доменных модулей."""
    
    def __init__(
        self,
        domain_name: str,
        max_docs_per_domain: int = 50,
        max_graph_depth: int = 3,
    ):
        self.domain_name = domain_name
        self.max_docs_per_domain = max_docs_per_domain
        self.max_graph_depth = max_graph_depth
        self._prompt_templates: Dict[str, Any] = {}
        self._quality_threshold: float = 0.7
    
    @abstractmethod
    def get_prompt_templates(self) -> Dict[str, Any]:
        """Получить промпты для домена."""
        pass
    
    @abstractmethod
    async def process(
        self,
        tenant_id: UUID | str,
        query: Dict[str, Any],
        retrieval_service: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Обработать запрос в рамках домена.
        
        Args:
            tenant_id: ID тенанта
            query: Запрос с параметрами
            retrieval_service: Сервис для retrieval (общий слой)
        
        Returns:
            Результат обработки
        """
        pass
    
    def get_quality_threshold(self) -> float:
        """Получить порог качества для домена."""
        return self._quality_threshold
    
    def set_quality_threshold(self, threshold: float):
        """Установить порог качества."""
        self._quality_threshold = threshold

