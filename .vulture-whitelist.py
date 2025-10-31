"""
Vulture whitelist for false positives.

[C7-ID: CODE-CLEANUP-008] Context7 best practice: coverage-guided whitelist generation
This file will be auto-generated based on pytest coverage:
    pytest --cov=. --cov-report=term-missing
    vulture . --make-whitelist > .vulture-whitelist.py

Manual entries for known false positives:
"""

# Test fixtures and markers
from pytest import fixture, mark

# FastAPI route handlers (called by framework)
from fastapi import APIRouter
router = APIRouter()
router.get("/")
router.post("/")

# Celery tasks (called by worker)
from celery import shared_task

# Alembic migrations (called by alembic)
from alembic import op

# Click CLI commands (called by click)
import click

# Decorators that preserve function signatures
def keep(func):
    """Decorator to mark functions that should be kept."""
    return func

@keep
def preserved_function():
    """Function marked with @keep decorator."""
    pass

