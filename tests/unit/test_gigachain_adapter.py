"""
Контрактные тесты для GigaChain адаптера
[C7-ID: AI-TESTS-001]

Проверяет совместимость с GigaChat и OpenRouter провайдерами
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import List, Dict, Any

from worker.ai_providers.gigachain_adapter import (
    GigaChainAdapter,
    ProviderConfig,
    TaggingConfig,
    TaggingResult,
)


def make_adapter(enable_feature_flags: bool = False):
    primary = ProviderConfig(
        name="gigachat",
        api_key="key",
        base_url="https://example.com",
        model="GigaChat",
        max_concurrent_requests=1,
    )
    fallback = ProviderConfig(
        name="openrouter",
        api_key="key",
        base_url="https://fallback",
        model="qwen",
        max_concurrent_requests=1,
    )
    config = TaggingConfig(
        prompt_version="v1.0.0",
        enable_feature_flags=enable_feature_flags,
    )
    return GigaChainAdapter(primary_config=primary, fallback_config=fallback, tagging_config=config)


@pytest.mark.asyncio
async def test_generate_tags_batch_returns_results(monkeypatch):
    adapter = make_adapter()
    expected = [TaggingResult(tags=["a", "b"], language="ru", processing_time_ms=10)]

    async def _fake_primary(texts, force_immediate=False):
        return expected

    monkeypatch.setattr(adapter, "_generate_tags_with_gigachat", _fake_primary)
    results = await adapter.generate_tags_batch(["text"], force_immediate=True)
    assert results == expected


@pytest.mark.asyncio
async def test_generate_tags_batch_fallback_on_exception(monkeypatch):
    adapter = make_adapter()

    async def _primary(_texts, force_immediate=False):
        raise RuntimeError("primary failed")

    async def _fallback(texts, force_immediate=False):
        return [TaggingResult(tags=["fallback"], language="ru", processing_time_ms=5)]

    monkeypatch.setattr(adapter, "_generate_tags_with_gigachat", _primary)
    monkeypatch.setattr(adapter, "_generate_tags_with_openrouter", _fallback)

    results = await adapter.generate_tags_batch(["text"], force_immediate=True)
    assert results[0].tags == ["fallback"]


@pytest.mark.asyncio
async def test_generate_tags_batch_empty_texts():
    adapter = make_adapter()
    results = await adapter.generate_tags_batch([])
    assert results == []


@pytest.mark.asyncio
async def test_generate_tags_batch_uses_feature_flags(monkeypatch):
    adapter = make_adapter(enable_feature_flags=True)

    class DummyFlags:
        def get_available_ai_providers(self):
            return ["gigachat"]

    monkeypatch.setattr("worker.ai_providers.gigachain_adapter.feature_flags", DummyFlags())

    async def _primary(texts, force_immediate=False):
        return [TaggingResult(tags=["ok"], language="ru") for _ in texts]

    monkeypatch.setattr(adapter, "_generate_tags_with_gigachat", _primary)

    results = await adapter.generate_tags_batch(["hello"])
    assert results[0].tags == ["ok"]


@pytest.mark.asyncio
async def test_generate_embeddings_batch_returns_empty():
    adapter = make_adapter()
    embeddings = await adapter.generate_embeddings_batch(["text"], force_immediate=True)
    assert embeddings == [[]]


def test_provider_config_defaults():
    config = ProviderConfig(name="gigachat", api_key="k", base_url="https://", model="GigaChat")
    assert config.max_tokens == 4000
    assert config.temperature == 0.1
    assert config.max_concurrent_requests == 1


def test_tagging_config_defaults():
    config = TaggingConfig()
    assert config.max_tags == 5
    assert config.prompt_template
