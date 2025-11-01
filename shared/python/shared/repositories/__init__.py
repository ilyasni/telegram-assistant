"""Shared repositories for database operations.

Context7 best practice: единые репозитории для доступа к данным
с соблюдением идемпотентности и транзакционной целостности.
"""

from .enrichment_repository import EnrichmentRepository

__all__ = ['EnrichmentRepository']

