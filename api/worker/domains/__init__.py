"""
Domain Modules - доменные модули для обработки различных типов запросов.

Performance guardrails:
- Общий RetrievalService для всех доменов
- Per-domain ограничения: max_docs_per_domain, max_graph_depth
"""

from api.worker.domains.base_domain import DomainModule
from api.worker.domains.digest_domain import DigestDomain
from api.worker.domains.trend_domain import TrendDomain
from api.worker.domains.domain_orchestrator import DomainOrchestrator, get_domain_orchestrator

__all__ = [
    "DomainModule",
    "DigestDomain",
    "TrendDomain",
    "DomainOrchestrator",
    "get_domain_orchestrator",
]
