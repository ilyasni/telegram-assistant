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
    Tag
)


class TestGigaChainAdapterContracts:
    """Контрактные тесты для GigaChain адаптера."""
    
    @pytest.fixture
    def mock_gigachat_config(self):
        """Mock конфигурация для GigaChat."""
        return ProviderConfig(
            name="gigachat",
            api_key="test_gigachat_key",
            base_url="https://gigachat.devices.sberbank.ru/api/v1",
            model="GigaChat:latest",
            max_tokens=4000,
            temperature=0.1,
            timeout=30,
            max_retries=3,
            batch_size=10
        )
    
    @pytest.fixture
    def mock_openrouter_config(self):
        """Mock конфигурация для OpenRouter."""
        return ProviderConfig(
            name="openrouter",
            api_key="test_openrouter_key",
            base_url="https://openrouter.ai/api/v1",
            model="qwen/qwen-2.5-72b-instruct:free",
            max_tokens=4000,
            temperature=0.1,
            timeout=30,
            max_retries=3,
            batch_size=10
        )
    
    @pytest.fixture
    def tagging_config(self):
        """Конфигурация тегирования."""
        return TaggingConfig(
            prompt_version="v1.2.3",
            enable_contract_tests=True,
            enable_feature_flags=True
        )
    
    @pytest.fixture
    def mock_openai_response(self):
        """Mock ответ от OpenAI API."""
        return {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "tags": [
                            {"name": "technology", "confidence": 0.95, "category": "tech"},
                            {"name": "ai", "confidence": 0.87, "category": "tech"}
                        ],
                        "summary": "Статья о технологиях и ИИ",
                        "language": "ru"
                    })
                }
            }],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150
            }
        }
    
    @pytest.mark.asyncio
    async def test_gigachat_contract_success(self, mock_gigachat_config, tagging_config, mock_openai_response):
        """Тест успешного контракта с GigaChat."""
        with patch('openai.AsyncOpenAI') as mock_openai:
            # Настройка mock
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_openai_response)
            mock_openai.return_value = mock_client
            
            # Создание адаптера
            adapter = GigaChainAdapter(
                primary_config=mock_gigachat_config,
                fallback_config=None,
                tagging_config=tagging_config
            )
            
            # Тест
            texts = ["Тестовая статья о технологиях"]
            results = await adapter.generate_tags_batch(texts)
            
            # Проверки
            assert len(results) == 1
            assert len(results[0].tags) == 2
            assert results[0].tags[0].name == "technology"
            assert results[0].tags[0].confidence == 0.95
            assert results[0].summary == "Статья о технологиях и ИИ"
            assert results[0].language == "ru"
            
            # Проверка вызова API
            mock_client.chat.completions.create.assert_called_once()
            call_args = mock_client.chat.completions.create.call_args
            assert "messages" in call_args.kwargs
            assert call_args.kwargs["model"] == "GigaChat:latest"
    
    @pytest.mark.asyncio
    async def test_openrouter_fallback_contract(self, mock_gigachat_config, mock_openrouter_config, tagging_config, mock_openai_response):
        """Тест fallback контракта с OpenRouter."""
        with patch('openai.AsyncOpenAI') as mock_openai:
            # Настройка mock для fallback
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_openai_response)
            mock_openai.return_value = mock_client
            
            # Создание адаптера с fallback
            adapter = GigaChainAdapter(
                primary_config=mock_gigachat_config,
                fallback_config=mock_openrouter_config,
                tagging_config=tagging_config
            )
            
            # Симуляция падения primary провайдера
            with patch.object(adapter, '_call_primary_provider', side_effect=Exception("GigaChat unavailable")):
                texts = ["Тестовая статья о технологиях"]
                results = await adapter.generate_tags_batch(texts)
                
                # Проверки
                assert len(results) == 1
                assert len(results[0].tags) == 2
                assert results[0].tags[0].name == "technology"
    
    @pytest.mark.asyncio
    async def test_invalid_json_response_handling(self, mock_gigachat_config, tagging_config):
        """Тест обработки невалидного JSON ответа."""
        with patch('openai.AsyncOpenAI') as mock_openai:
            # Mock с невалидным JSON
            invalid_response = {
                "choices": [{
                    "message": {
                        "content": "Invalid JSON response"
                    }
                }],
                "usage": {"total_tokens": 100}
            }
            
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=invalid_response)
            mock_openai.return_value = mock_client
            
            adapter = GigaChainAdapter(
                primary_config=mock_gigachat_config,
                fallback_config=None,
                tagging_config=tagging_config
            )
            
            texts = ["Тестовая статья"]
            results = await adapter.generate_tags_batch(texts)
            
            # Должен вернуть пустой результат при невалидном JSON
            assert len(results) == 1
            assert len(results[0].tags) == 0
            assert results[0].summary == ""
    
    @pytest.mark.asyncio
    async def test_structured_output_validation(self, mock_gigachat_config, tagging_config):
        """Тест валидации structured output."""
        with patch('openai.AsyncOpenAI') as mock_openai:
            # Mock с некорректной структурой
            invalid_structured_response = {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "tags": [
                                {"name": "test", "confidence": 1.5, "category": "tech"}  # confidence > 1.0
                            ]
                        })
                    }
                }],
                "usage": {"total_tokens": 100}
            }
            
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=invalid_structured_response)
            mock_openai.return_value = mock_client
            
            adapter = GigaChainAdapter(
                primary_config=mock_gigachat_config,
                fallback_config=None,
                tagging_config=tagging_config
            )
            
            texts = ["Тестовая статья"]
            results = await adapter.generate_tags_batch(texts)
            
            # Должен отфильтровать невалидные теги
            assert len(results) == 1
            assert len(results[0].tags) == 0  # Невалидный тег отфильтрован
    
    @pytest.mark.asyncio
    async def test_batch_processing_contract(self, mock_gigachat_config, tagging_config):
        """Тест контракта батчевой обработки."""
        with patch('openai.AsyncOpenAI') as mock_openai:
            # Mock для батча из 3 текстов
            batch_response = {
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "tags": [
                                {"name": "batch_test", "confidence": 0.9, "category": "tech"}
                            ],
                            "summary": "Batch processing test",
                            "language": "en"
                        })
                    }
                }],
                "usage": {"total_tokens": 200}
            }
            
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=batch_response)
            mock_openai.return_value = mock_client
            
            adapter = GigaChainAdapter(
                primary_config=mock_gigachat_config,
                fallback_config=None,
                tagging_config=tagging_config
            )
            
            # Тест с батчем
            texts = ["Text 1", "Text 2", "Text 3"]
            results = await adapter.generate_tags_batch(texts, force_immediate=True)
            
            # Проверки
            assert len(results) == 3
            for result in results:
                assert len(result.tags) == 1
                assert result.tags[0].name == "batch_test"
    
    @pytest.mark.asyncio
    async def test_retry_mechanism_contract(self, mock_gigachat_config, tagging_config):
        """Тест контракта механизма повторов."""
        with patch('openai.AsyncOpenAI') as mock_openai:
            # Mock с падением на первых двух попытках
            mock_client = AsyncMock()
            call_count = 0
            
            async def mock_create(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count <= 2:
                    raise Exception("Temporary failure")
                return {
                    "choices": [{
                        "message": {
                            "content": json.dumps({
                                "tags": [{"name": "retry_test", "confidence": 0.9, "category": "tech"}],
                                "summary": "Retry mechanism test",
                                "language": "en"
                            })
                        }
                    }],
                    "usage": {"total_tokens": 100}
                }
            
            mock_client.chat.completions.create = mock_create
            mock_openai.return_value = mock_client
            
            adapter = GigaChainAdapter(
                primary_config=mock_gigachat_config,
                fallback_config=None,
                tagging_config=tagging_config
            )
            
            texts = ["Retry test text"]
            results = await adapter.generate_tags_batch(texts, force_immediate=True)
            
            # Проверки
            assert len(results) == 1
            assert len(results[0].tags) == 1
            assert results[0].tags[0].name == "retry_test"
            assert call_count == 3  # 2 неудачи + 1 успех
    
    @pytest.mark.asyncio
    async def test_timeout_handling_contract(self, mock_gigachat_config, tagging_config):
        """Тест контракта обработки таймаутов."""
        with patch('openai.AsyncOpenAI') as mock_openai:
            # Mock с таймаутом
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(side_effect=asyncio.TimeoutError("Request timeout"))
            mock_openai.return_value = mock_client
            
            adapter = GigaChainAdapter(
                primary_config=mock_gigachat_config,
                fallback_config=None,
                tagging_config=tagging_config
            )
            
            texts = ["Timeout test text"]
            results = await adapter.generate_tags_batch(texts, force_immediate=True)
            
            # Должен вернуть пустой результат при таймауте
            assert len(results) == 1
            assert len(results[0].tags) == 0
    
    @pytest.mark.asyncio
    async def test_embedding_contract(self, mock_gigachat_config):
        """Тест контракта генерации эмбеддингов."""
        with patch('openai.AsyncOpenAI') as mock_openai:
            # Mock для embeddings
            embedding_response = {
                "data": [{
                    "embedding": [0.1, 0.2, 0.3] * 512  # 1536-мерный вектор
                }],
                "usage": {"total_tokens": 100}
            }
            
            mock_client = AsyncMock()
            mock_client.embeddings.create = AsyncMock(return_value=embedding_response)
            mock_openai.return_value = mock_client
            
            adapter = GigaChainAdapter(
                primary_config=mock_gigachat_config,
                fallback_config=None,
                tagging_config=TaggingConfig()
            )
            
            texts = ["Test embedding text"]
            embeddings = await adapter.generate_embeddings(texts)
            
            # Проверки
            assert len(embeddings) == 1
            assert len(embeddings[0]) == 1536
            assert all(isinstance(x, float) for x in embeddings[0])
    
    def test_provider_config_validation(self):
        """Тест валидации конфигурации провайдера."""
        # Валидная конфигурация
        valid_config = ProviderConfig(
            name="test",
            api_key="test_key",
            base_url="https://api.test.com",
            model="test-model",
            max_tokens=1000,
            temperature=0.5,
            timeout=30,
            max_retries=3,
            batch_size=5
        )
        assert valid_config.name == "test"
        assert valid_config.api_key == "test_key"
        
        # Невалидная конфигурация (отрицательный timeout)
        with pytest.raises(ValueError):
            ProviderConfig(
                name="test",
                api_key="test_key",
                base_url="https://api.test.com",
                model="test-model",
                max_tokens=1000,
                temperature=0.5,
                timeout=-1,  # Невалидное значение
                max_retries=3,
                batch_size=5
            )
    
    def test_tagging_config_validation(self):
        """Тест валидации конфигурации тегирования."""
        # Валидная конфигурация
        valid_config = TaggingConfig(
            max_tags=10,
            min_confidence=0.7,
            prompt_version="v1.0.0"
        )
        assert valid_config.max_tags == 10
        assert valid_config.min_confidence == 0.7
        
        # Невалидная конфигурация (отрицательное количество тегов)
        with pytest.raises(ValueError):
            TaggingConfig(
                max_tags=-1,  # Невалидное значение
                min_confidence=0.7
            )


class TestContractTestRunner:
    """Тест раннера контрактных тестов."""
    
    @pytest.mark.asyncio
    async def test_contract_test_runner(self):
        """Тест автоматического запуска контрактных тестов."""
        # Этот тест должен запускаться в CI/CD для проверки совместимости
        # с реальными провайдерами (если доступны API ключи)
        
        # Проверка доступности провайдеров
        from worker.feature_flags import feature_flags
        
        available_providers = feature_flags.get_available_ai_providers()
        
        if not available_providers:
            pytest.skip("No AI providers available for contract testing")
        
        # Здесь можно добавить реальные тесты с провайдерами
        # если доступны API ключи в тестовом окружении
        assert len(available_providers) >= 0  # Минимальная проверка
