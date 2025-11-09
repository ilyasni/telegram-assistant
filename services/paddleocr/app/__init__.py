"""
PaddleOCR microservice (CPU-only) for Phase 0 benchmarking.

Exposes FastAPI application via `server.app`.
"""

from .service import PaddleOCRService, get_default_service

__all__ = ["PaddleOCRService", "get_default_service"]

