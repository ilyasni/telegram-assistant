"""
Digest Domain - доменный модуль для групповых дайджестов.
"""

from __future__ import annotations

from typing import Dict, Any, Optional
from uuid import UUID

from api.worker.domains.base_domain import DomainModule
from api.worker.services.retrieval_service import RetrievalService


class DigestDomain(DomainModule):
    """Доменный модуль для групповых дайджестов."""
    
    def __init__(self):
        super().__init__(
            domain_name="digest",
            max_docs_per_domain=30,  # Ограничение для digest
            max_graph_depth=2,
        )
        self._quality_threshold = 0.75
    
    def get_prompt_templates(self) -> Dict[str, Any]:
        """Получить промпты для digest домена."""
        # Импортируем промпты из group_digest_agent
        from api.worker.tasks.group_digest_agent import (
            semantic_segmenter_prompt_v1,
            emotion_analyzer_prompt_v1,
            role_classifier_prompt_v1,
            topic_synthesizer_prompt_v1,
            digest_composer_prompt_v2,
            quality_evaluator_prompt_v1,
        )
        
        return {
            "segmenter": semantic_segmenter_prompt_v1(),
            "emotion": emotion_analyzer_prompt_v1(),
            "roles": role_classifier_prompt_v1(),
            "topics": topic_synthesizer_prompt_v1(),
            "synthesis": digest_composer_prompt_v2(),
            "evaluation": quality_evaluator_prompt_v1(),
        }
    
    async def process(
        self,
        tenant_id: UUID | str,
        query: Dict[str, Any],
        retrieval_service: Optional[RetrievalService] = None,
    ) -> Dict[str, Any]:
        """
        Обработать запрос на дайджест.
        
        Использует существующий GroupDigestOrchestrator.
        Performance: Использует общий retrieval_service для получения документов.
        """
        from api.worker.tasks.group_digest_agent import GroupDigestOrchestrator
        import asyncio
        
        orchestrator = GroupDigestOrchestrator()
        
        # Если передан retrieval_service, можно использовать его для предварительной фильтрации
        # Но для digest pipeline пока используем существующий механизм
        # TODO: Интеграция retrieval_service для оптимизации
        
        # Вызов существующего генератора дайджестов
        # generate - синхронный метод, но process - async, поэтому используем run_in_executor
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, orchestrator.generate, query)
        
        return result

