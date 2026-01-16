"""
Domain Orchestrator - оркестратор для маршрутизации запросов к доменным модулям.

Performance guardrails:
- Использует Context Router для определения домена
- Общий RetrievalService для всех доменов
- Per-domain ограничения через DomainModule
"""

from __future__ import annotations

from typing import Dict, Any, Optional
from uuid import UUID

import structlog

from api.worker.agents.context_router_agent import get_context_router
from api.worker.services.retrieval_service import get_retrieval_service, RetrievalService

logger = structlog.get_logger(__name__)


class DomainOrchestrator:
    """Оркестратор для маршрутизации запросов к доменным модулям."""
    
    def __init__(
        self,
        retrieval_service: Optional[RetrievalService] = None,
    ):
        """
        Инициализация DomainOrchestrator.
        
        Args:
            retrieval_service: Общий RetrievalService для всех доменов
        """
        self._context_router = get_context_router()
        self._retrieval_service = retrieval_service or get_retrieval_service()
        
        # Инициализация доменных модулей (ленивая загрузка для избежания циклических импортов)
        self._domains: Dict[str, Any] = {}
    
    async def process(
        self,
        query: str,
        tenant_id: UUID | str,
        user_id: Optional[UUID | str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Обработать запрос через доменные модули.
        
        Performance: Использует Context Router для определения домена.
        
        Args:
            query: Запрос пользователя
            tenant_id: ID тенанта
            user_id: ID пользователя
            **kwargs: Дополнительные параметры
        
        Returns:
            Результат обработки доменом
        """
        # Маршрутизация через Context Router
        route_result = self._context_router.route(
            query=query,
            tenant_id=str(tenant_id),
            user_id=str(user_id) if user_id else None,
        )
        
        # Маппинг route_type в domain
        route_to_domain = {
            "digest": "digest",
            "trend": "trend",
            "qna": "digest",  # Q&A → digest (можно создать отдельный QnADomain)
            "search": "digest",  # Search → digest
            "enrichment": "digest",  # Enrichment → digest
        }
        
        domain_name = route_to_domain.get(route_result.route_type, "digest")
        
        logger.info(
            "domain_orchestrator.route",
            query=query[:50],
            route_type=route_result.route_type,
            domain=domain_name,
            confidence=route_result.confidence,
            method=route_result.method,
        )
        
        # Получение доменного модуля (ленивая инициализация)
        if domain_name not in self._domains:
            if domain_name == "digest":
                from api.worker.domains.digest_domain import DigestDomain
                self._domains[domain_name] = DigestDomain()
            elif domain_name == "trend":
                from api.worker.domains.trend_domain import TrendDomain
                self._domains[domain_name] = TrendDomain()
            else:
                # Fallback на digest
                logger.warning(
                    "domain_orchestrator.domain_not_found",
                    domain=domain_name,
                    falling_back_to="digest"
                )
                from api.worker.domains.digest_domain import DigestDomain
                self._domains[domain_name] = DigestDomain()
                domain_name = "digest"
        
        domain = self._domains.get(domain_name)
        
        # Подготовка query для домена
        domain_query = {
            "query": query,
            "text": query,
            "tenant_id": str(tenant_id),
            "user_id": str(user_id) if user_id else None,
            **kwargs,
        }
        
        # Обработка через доменный модуль
        try:
            result = await domain.process(
                tenant_id=tenant_id,
                query=domain_query,
                retrieval_service=self._retrieval_service,
            )
            
            result["domain"] = domain_name
            result["route_type"] = route_result.route_type
            result["confidence"] = route_result.confidence
            result["method"] = route_result.method
            
            return result
        
        except Exception as e:
            logger.error(
                "domain_orchestrator.process_failed",
                domain=domain_name,
                error=str(e),
                error_type=type(e).__name__,
            )
            return {
                "domain": domain_name,
                "status": "error",
                "error": str(e),
                "error_type": type(e).__name__,
            }


# Singleton instance
_domain_orchestrator: Optional[DomainOrchestrator] = None


def get_domain_orchestrator(
    retrieval_service: Optional[RetrievalService] = None,
) -> DomainOrchestrator:
    """Получить экземпляр DomainOrchestrator."""
    global _domain_orchestrator
    if retrieval_service:
        return DomainOrchestrator(retrieval_service=retrieval_service)
    if _domain_orchestrator is None:
        _domain_orchestrator = DomainOrchestrator()
    return _domain_orchestrator

