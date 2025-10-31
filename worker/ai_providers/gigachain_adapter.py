"""
GigaChain адаптер для интеграции с GigaChat
Поддерживает fallback на OpenRouter API
Включает structured output, retries, batching и метрики
Context7 best practice: Соблюдение лимита GigaChat в 1 поток
"""

import asyncio
import json
import logging
import os
import time
import uuid
from typing import List, Dict, Any, Optional, Union
from datetime import datetime, timezone
from dataclasses import dataclass

import openai
from pydantic import BaseModel, Field
from prometheus_client import Counter, Histogram, Gauge

from feature_flags import feature_flags
from prompts.tagging import STRICT_TAGGING_PROMPT

logger = logging.getLogger(__name__)

# ============================================================================
# МЕТРИКИ PROMETHEUS
# ============================================================================

tagging_requests_total = Counter(
    'tagging_requests_total',
    'Total tagging requests',
    ['provider', 'model', 'success']
)

tagging_latency_seconds = Histogram(
    'tagging_latency_seconds',
    'Tagging operation latency',
    ['provider', 'model']
)

tagging_failures_total = Counter(
    'tagging_failures_total',
    'Total tagging failures',
    ['provider', 'reason']
)

# Метрики для rate limiting
gigachat_rate_limit_total = Counter(
    'gigachat_rate_limit_total',
    'GigaChat rate limit hits',
    ['retry_attempt']
)

gigachat_request_duration = Histogram(
    'gigachat_request_duration_seconds',
    'GigaChat API request duration',
    ['status']
)

tagging_provider_used = Counter(
    'tagging_provider_used_total',
    'AI provider used for tagging',
    ['provider']  # gigachat, openrouter
)

# ============================================================================
# КОНФИГУРАЦИЯ
# ============================================================================

@dataclass
class ProviderConfig:
    """Конфигурация AI провайдера."""
    name: str
    api_key: str
    base_url: str
    model: str
    max_tokens: int = 4000
    temperature: float = 0.1
    timeout: int = 30
    max_retries: int = 3
    max_concurrent_requests: int = 1

@dataclass
class TaggingConfig:
    """Конфигурация тегирования."""
    prompt_template: str = STRICT_TAGGING_PROMPT
    structured_output: bool = False
    max_tags: int = 5
    min_confidence: float = 0.0
    prompt_version: str = "v2.0.0"
    enable_feature_flags: bool = True

# ============================================================================
# СХЕМЫ ДАННЫХ
# ============================================================================

class TaggingResult(BaseModel):
    """Результат тегирования."""
    tags: List[str] = Field(default_factory=list, description="Список тегов")
    language: str = Field(default="ru", description="Язык текста")
    processing_time_ms: int = Field(default=0, description="Время обработки в мс")

# ============================================================================
# GIGACHAIN АДАПТЕР
# ============================================================================

class GigaChainAdapter:
    """Адаптер для работы с GigaChat через OpenAI API."""
    
    def __init__(
        self,
        primary_config: ProviderConfig,
        fallback_config: Optional[ProviderConfig] = None,
        tagging_config: TaggingConfig = TaggingConfig()
    ):
        self.primary_config = primary_config
        self.fallback_config = fallback_config
        self.tagging_config = tagging_config
        
        # Семафор для соблюдения лимита GigaChat в 1 поток
        self._request_semaphore = asyncio.Semaphore(primary_config.max_concurrent_requests)
        
        logger.info(f"Initialized GigaChain adapter with primary: {primary_config.name}")
    
    # ========================================================================
    # ТЕГИРОВАНИЕ
    # ========================================================================
    
    async def generate_tags_batch(
        self, 
        texts: List[str],
        force_immediate: bool = False
    ) -> List[TaggingResult]:
        """
        Батчевое тегирование текстов с соблюдением лимита GigaChat.
        
        Context7 best practice: Использует семафор для соблюдения лимита в 1 поток.
        """
        if not texts:
            return []
        
        # Проверка feature flags
        if self.tagging_config.enable_feature_flags:
            available_providers = feature_flags.get_available_ai_providers()
            if not available_providers:
                logger.warning("No AI providers available, using mock results")
                return [TaggingResult(tags=[], language="unknown")] * len(texts)
        
        logger.info(f"Generating tags for batch of {len(texts)} texts")
        
        # Используем семафор для соблюдения лимита в 1 поток
        async with self._request_semaphore:
            primary_name = (self.primary_config.name or "").lower()
            fallback_name = (self.fallback_config.name if self.fallback_config else "").lower()

            async def call_provider(name: str) -> List[TaggingResult]:
                if name == "gigachat":
                    return await self._generate_tags_with_gigachat(texts)
                elif name == "openrouter":
                    return await self._generate_tags_with_openrouter(texts)
                # Неизвестный провайдер – вернуть пустые
                logger.warning("Unknown provider name, returning empty results", extra={"provider": name})
                return [TaggingResult(tags=[], language="unknown")] * len(texts)

            # 1) Вызываем primary провайдера
            try:
                results = await call_provider(primary_name)
            except Exception as e:
                logger.error("Primary provider failed", extra={"provider": primary_name, "error": str(e)})
                results = None

            # 2) Если primary вернул пустые теги для всех текстов – пробуем fallback
            def all_empty(res: Optional[List[TaggingResult]]) -> bool:
                return res is None or all((not r.tags) for r in res)

            if all_empty(results) and fallback_name:
                try:
                    logger.info("Falling back to secondary provider due to empty tags", extra={"fallback": fallback_name})
                    fallback_results = await call_provider(fallback_name)
                    # Берём fallback если он дал непустые теги хотя бы для одного текста
                    if not all_empty(fallback_results):
                        return fallback_results
                    # Иначе используем исходные результаты (все пустые)
                    results = results or fallback_results
                except Exception as fallback_e:
                    logger.error("Fallback provider failed", extra={"provider": fallback_name, "error": str(fallback_e)})

            # 3) Если всё ещё нет результатов – вернуть заглушки
            return results or ([TaggingResult(tags=[], language="unknown")] * len(texts))
    
    async def _generate_tags_with_gigachat(self, texts: List[str]) -> List[TaggingResult]:
        """
        Генерация тегов через GigaChat с retry logic.
        
        Context7: [C7-ID: rate-limit-backoff-003] - resilient API calls
        Документация: https://github.com/ai-forever/gpt2giga
        """
        max_retries = 5
        base_delay = 1
        
        for attempt in range(max_retries):
            try:
                return await self._call_gigachat_api(texts)
                
            except Exception as e:
                if hasattr(e, 'response') and e.response.status_code == 429:
                    # GigaChat rate limit (согласно документации)
                    retry_after = int(e.response.headers.get('Retry-After', base_delay * (2 ** attempt)))
                    
                    logger.warning(
                        "GigaChat rate limit hit",
                        attempt=attempt + 1,
                        retry_after=retry_after,
                        max_retries=max_retries
                    )
                    
                    # Prometheus метрика
                    gigachat_rate_limit_total.labels(retry_attempt=attempt).inc()
                    
                    # Exponential backoff с jitter
                    import random
                    jitter = random.uniform(0, 0.1 * retry_after)
                    await asyncio.sleep(retry_after + jitter)
                    
                    continue
                else:
                    raise
        
        # После всех попыток - fallback на OpenRouter
        logger.error("GigaChat failed after retries, falling back to OpenRouter")
        return await self._generate_tags_with_openrouter(texts)
    
    async def _call_gigachat_api(self, texts: List[str]) -> List[TaggingResult]:
        """Вызов GigaChat API через gpt2giga-proxy."""
        import requests
        
        # Используем gpt2giga-proxy как OpenAI API
        api_base = os.getenv('OPENAI_API_BASE', 'http://gpt2giga-proxy:8090/v1')
        api_key = os.getenv('OPENAI_API_KEY', 'dummy')
        
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }
        
        results = []
        
        for text in texts:
            if not text.strip():
                results.append(TaggingResult(tags=[], language="unknown"))
                continue
            
            # Создаём строгий промпт из централизованного шаблона
            prompt = STRICT_TAGGING_PROMPT.format(text=text[:1000])
            
            start_time = time.time()
            try:
                response = requests.post(
                    f'{api_base}/chat/completions',
                    headers=headers,
                    json={
                        'model': 'GigaChat',
                        'messages': [{'role': 'user', 'content': prompt}],
                        'max_tokens': 100,
                        'temperature': 0.1
                    },
                    timeout=30
                )
                
                # Prometheus метрики
                duration = time.time() - start_time
                gigachat_request_duration.labels(status="success" if response and response.status_code == 200 else "error").observe(duration)
                tagging_provider_used.labels(provider="gigachat").inc()
                
                if response and response.status_code == 200:
                    result = response.json()
                    content = result['choices'][0]['message']['content'].strip()
                    
                    try:
                        # Парсим JSON ответ
                        import json
                        import re
                        
                        # Context7: [C7-ID: json-parsing-fix-001] - исправление неправильных кавычек в GigaChat ответах
                        # GigaChat иногда возвращает теги с неправильными кавычками: «вместо "
                        content_fixed = content.replace('«', '"').replace('»', '"')
                        
                        tags = json.loads(content_fixed)
                        if isinstance(tags, list):
                            # Фильтруем пустые теги и ограничиваем количество
                            tags = [tag.strip() for tag in tags if tag.strip()][:5]
                        else:
                            tags = []
                    except (json.JSONDecodeError, TypeError) as e:
                        logger.warning("Failed to parse tags JSON", extra={"content": content[:100], "error": str(e)})
                        # Fallback: попробуем извлечь теги через regex
                        import re
                        tag_pattern = r'["«]([^"»]+)["»]'
                        matches = re.findall(tag_pattern, content)
                        tags = [match.strip() for match in matches if match.strip()][:5]
                        if tags:
                            logger.info(f"Extracted tags via regex: {tags}")
                        else:
                            tags = []
                    
                    results.append(TaggingResult(tags=tags, language="ru"))
                else:
                    error_msg = f"GigaChat API error: {response.status_code if response else 'No response'} - {response.text if response else 'No response'}"
                    logger.error(error_msg)
                    # Выбрасываем исключение для retry logic
                    if response:
                        response.raise_for_status()
                    else:
                        raise Exception("No response from GigaChat API")
            except Exception as e:
                logger.error("GigaChat API request failed", extra={"error": str(e)})
                # Выбрасываем исключение для retry logic
                raise
        
        return results
    
    async def _generate_tags_with_openrouter(self, texts: List[str]) -> List[TaggingResult]:
        """Генерация тегов через OpenRouter API."""
        try:
            import requests
            
            api_key = os.getenv('OPENROUTER_API_KEY')
            api_base = os.getenv('OPENROUTER_API_BASE', 'https://openrouter.ai/api/v1')
            
            headers = {
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            }
            
            results = []
            
            for text in texts:
                if not text.strip():
                    results.append(TaggingResult(tags=[], language="unknown"))
                    continue
                
                # Создаем промпт для тегирования
                prompt = f"""Проанализируй текст и верни 1-5 тегов в формате JSON массива строк.
                
Текст: {text[:500]}

Ответь только JSON массивом тегов, например: ["технологии", "искусственный интеллект"]"""
                
                response = requests.post(
                    f'{api_base}/chat/completions',
                    headers=headers,
                    json={
                        'model': 'qwen/qwen-2.5-72b-instruct:free',
                        'messages': [{'role': 'user', 'content': prompt}],
                        'max_tokens': 100,
                        'temperature': 0.1
                    },
                    timeout=30
                )
                
                if response.status_code == 200:
                    result = response.json()
                    content = result['choices'][0]['message']['content'].strip()
                    
                    # Парсим JSON ответ
                    try:
                        if content.startswith('```json'):
                            content = content[7:-3]
                        elif content.startswith('```'):
                            content = content[3:-3]
                        
                        tags = json.loads(content)
                        if isinstance(tags, list):
                            results.append(TaggingResult(tags=tags[:5], language="ru"))
                        else:
                            results.append(TaggingResult(tags=[], language="ru"))
                    except json.JSONDecodeError:
                        logger.warning(f"Failed to parse tags JSON: {content}")
                        results.append(TaggingResult(tags=[], language="ru"))
                else:
                    logger.error(f"OpenRouter API error: {response.status_code} - {response.text}")
                    results.append(TaggingResult(tags=[], language="unknown"))
            
            return results
            
        except Exception as e:
            logger.error(f"OpenRouter tagging error: {e}")
            return [TaggingResult(tags=[], language="unknown")] * len(texts)
    
    # ========================================================================
    # EMBEDDINGS (заглушка)
    # ========================================================================
    
    async def generate_embeddings_batch(
        self, 
        texts: List[str],
        force_immediate: bool = False
    ) -> List[Any]:
        """Генерация эмбеддингов (заглушка)."""
        # Возвращаем пустые эмбеддинги для совместимости
        return [[] for _ in texts]
    
    async def close(self):
        """Закрытие соединений."""
        logger.info("GigaChain adapter connections closed")

# ============================================================================
# MOCK АДАПТЕР
# ============================================================================

class MockGigaChainAdapter:
    """Mock адаптер для тестирования."""
    
    async def generate_tags_batch(
        self, 
        texts: List[str],
        force_immediate: bool = False
    ) -> List[TaggingResult]:
        """Mock тегирование."""
        return [TaggingResult(tags=["mock", "test"], language="ru") for _ in texts]
    
    async def generate_embeddings_batch(
        self, 
        texts: List[str],
        force_immediate: bool = False
    ) -> List[Any]:
        """Mock эмбеддинги."""
        return [[0.1] * 128 for _ in texts]
    
    async def close(self):
        """Mock закрытие."""
        pass

# ============================================================================
# FACTORY FUNCTION
# ============================================================================

async def create_gigachain_adapter(
    gigachat_api_key: Optional[str] = None,
    gigachat_credentials: Optional[str] = None,
    openrouter_api_key: Optional[str] = None,
    openrouter_model: Optional[str] = None
) -> GigaChainAdapter:
    """Создание GigaChain адаптера с fallback логикой."""
    providers = feature_flags.get_available_ai_providers()
    
    if not providers:
        logger.warning("No AI providers available, using mock adapter")
        return MockGigaChainAdapter()
    
    # OpenRouter конфигурация
    openrouter_config = ProviderConfig(
        name="openrouter",
        api_key=openrouter_api_key or os.getenv("OPENROUTER_API_KEY", ""),
        base_url=os.getenv("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1"),
        model=openrouter_model or os.getenv("OPENROUTER_MODEL", "qwen/qwen-2.5-72b-instruct:free"),
        max_concurrent_requests=1
    )
    
    # GigaChat конфигурация (если доступен)
    gigachat_config = None
    if "gigachat" in providers:
        gigachat_config = ProviderConfig(
            name="gigachat",
            api_key=gigachat_api_key or os.getenv("OPENAI_API_KEY", ""),
            base_url=os.getenv("OPENAI_API_BASE", "http://gpt2giga-proxy:8090"),
            model="GigaChat",
            max_concurrent_requests=1
        )
    
    # Создаем адаптер
    adapter = GigaChainAdapter(
        primary_config=openrouter_config,
        fallback_config=gigachat_config,
        tagging_config=TaggingConfig()
    )
    
    logger.info("GigaChain adapter created successfully")
    return adapter
