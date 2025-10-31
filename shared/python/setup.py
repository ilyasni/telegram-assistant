"""
Setup script for shared package.

[C7-ID: CODE-CLEANUP-012] Context7 best practice: shared пакет с editable install
"""

from setuptools import setup, find_packages

setup(
    name="shared",
    version="0.1.0",
    description="Shared utilities for Telegram Assistant microservices",
    packages=find_packages(),
    python_requires=">=3.11",
    install_requires=[
        "pydantic>=2.0.0",
        "pydantic-settings>=2.0.0",
    ],
    extras_require={
        "logging": ["structlog>=23.0.0"],
    },
)

