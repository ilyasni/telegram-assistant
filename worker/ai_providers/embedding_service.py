"""
Унифицированный сервис генерации эмбеддингов.
[C7-ID: AI-EMBEDDING-SERVICE-001]
Context7 best practice: retry logic для resilient API calls
"""

import asyncio
import logging
import unicodedata
import re
from typing import List, Optional, Dict, Any
from abc import ABC, abstractmethod

import structlog
from prometheus_client import Counter, Histogram

from config import settings
from services.retry_policy import create_retry_decorator, DEFAULT_RETRY_CONFIG, classify_error, ErrorCategory

logger = structlog.get_logger()

# Метрики
embedding_requests_total = Counter(
    'embedding_requests_total',
    'Total embedding generation requests',
    ['provider', 'model', 'status']
)

embedding_latency_seconds = Histogram(
    'embedding_latency_seconds',
    'Embedding generation latency',
    ['provider', 'model']
)

# ============================================================================
# TEXT PREPROCESSING
# ============================================================================

def normalize_text(s: str) -> str:
    """
    [C7-ID: EMBEDDING-TEXT-NORM-001] Нормализация текста для эмбеддингов.
    - NFC normalization
    - Удаление zero-width символов
    - Схлопывание последовательностей пробелов
    """
    s = unicodedata.normalize("NFC", s.replace("\u200b", ""))
    s = re.sub(r"\s+", " ", s).strip()
    return s

def approx_tokens(s: str) -> int:
    """
    [C7-ID: EMBEDDING-TOKEN-ESTIMATE-001] Эвристическая оценка токенов.
    4 символа ≈ 1 токен (смешанный русский/английский текст).
    """
    return max(1, len(s) // 4)

def truncate_by_tokens(text: str, max_tokens: int = 8192) -> str:
    """
    [C7-ID: EMBEDDING-TRUNCATE-001] Обрезка текста по токен-лимиту.
    """
    if approx_tokens(text) > max_tokens:
        truncated = text[: max_tokens * 4]
        logger.warning("embedding_text_truncated_tokens", 
                      original_tokens=approx_tokens(text),
                      truncated_tokens=max_tokens)
        return truncated
    return text

# ============================================================================
# ABSTRACT PROVIDER
# ============================================================================

class EmbeddingProvider(ABC):
    """Абстрактный провайдер эмбеддингов."""
    
    @abstractmethod
    async def embed_text(self, text: str) -> List[float]:
        """Генерация эмбеддинга для текста."""
        pass
    
    @abstractmethod
    def get_dimension(self) -> int:
        """Размерность эмбеддинга."""
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """Проверка доступности провайдера."""
        pass

# ============================================================================
# GIGACHAT PROVIDER
# ============================================================================

class GigaChatEmbeddingProvider(EmbeddingProvider):
    """
    [C7-ID: AI-GIGACHAT-EMBED-001] Провайдер эмбеддингов через GigaChat API.
    Использует gpt2giga proxy для OpenAI-совместимого интерфейса.
    """
    
    def __init__(self, adapter):
        self.adapter = adapter
        self.model = settings.GIGACHAT_EMBEDDINGS_MODEL
        # GigaChat EmbeddingsGigaR возвращает 2560 измерений
        self.dimension = 2560
    
    def _embed_text_internal(self, text: str) -> List[float]:
        """
        Внутренний метод генерации эмбеддинга без retry.
        Context7: [C7-ID: retry-embedding-001] - retry wrapper применяется к этому методу
        """
        import time
        import requests
        import os
        from requests.exceptions import RequestException, ConnectionError, Timeout
        
        start_time = time.time()
        
        # Предобработка текста
        text = normalize_text(text)
        text = truncate_by_tokens(text, max_tokens=8192)
        
        # Прямой HTTP запрос к gpt2giga proxy
        proxy_url = os.getenv("GIGACHAT_PROXY_URL", "http://gpt2giga-proxy:8090")
        url = f"{proxy_url}/v1/embeddings"
        
        # Авторизация через credentials (как в тесте)
        credentials = os.getenv("GIGACHAT_CREDENTIALS")
        scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
        auth_header = f"giga-cred-{credentials}:{scope}"
        
        response = requests.post(
            url,
            json={
                "input": text,
                "model": "any"  # gpt2giga сам отправит на GPT2GIGA_EMBEDDINGS
            },
            headers={
                "Authorization": f"Bearer {auth_header}",
                "Content-Type": "application/json"
            },
            timeout=30
        )
        
        if response.status_code != 200:
            # Преобразуем HTTP ошибки в исключения для retry логики
            error_msg = f"HTTP {response.status_code}: {response.text}"
            if response.status_code >= 500:
                # Server errors - retryable
                raise ConnectionError(error_msg)
            elif response.status_code == 429:
                # Rate limit - retryable
                raise Timeout(error_msg)
            else:
                # Client errors - non-retryable
                raise ValueError(error_msg)
        
        data = response.json()
        if "data" not in data or not data["data"]:
            raise ValueError("No embedding data in response")
        
        embedding = data["data"][0]["embedding"]
        if not embedding or len(embedding) != self.dimension:
            raise ValueError(f"Invalid embedding: len={len(embedding)}, expected={self.dimension}")
        
        # Метрики успеха
        embedding_requests_total.labels(
            provider='gigachat',
            model=self.model,
            status='success'
        ).inc()
        
        embedding_latency_seconds.labels(
            provider='gigachat',
            model=self.model
        ).observe(time.time() - start_time)
        
        return embedding
    
    async def embed_text(self, text: str) -> List[float]:
        """
        Генерация эмбеддинга через gpt2giga proxy с retry логикой.
        Context7: [C7-ID: retry-embedding-001] - resilient API calls с exponential backoff
        """
        import time
        import requests
        from requests.exceptions import RequestException, ConnectionError, Timeout
        
        # Context7 best practice: retry для network errors и server errors
        retry_decorator = create_retry_decorator(
            config=DEFAULT_RETRY_CONFIG,
            operation_name="gigachat_embedding"
        )
        
        @retry_decorator
        def _call_with_retry(text: str) -> List[float]:
            try:
                return self._embed_text_internal(text)
            except (ConnectionError, Timeout, RequestException) as e:
                # Network/connection errors - retryable
                error_category = classify_error(e)
                if error_category == ErrorCategory.NON_RETRYABLE_VALIDATION:
                    # Не retry для validation errors
                    embedding_requests_total.labels(
                        provider='gigachat',
                        model=self.model,
                        status='error'
                    ).inc()
                    raise
                # Пробрасываем для retry
                raise
            except Exception as e:
                error_category = classify_error(e)
                if error_category != ErrorCategory.NON_RETRYABLE_VALIDATION:
                    # Retry для network-like errors
                    raise ConnectionError(str(e))
                # Validation errors - не retry
                embedding_requests_total.labels(
                    provider='gigachat',
                    model=self.model,
                    status='error'
                ).inc()
                raise
        
        try:
            # Выполняем синхронный вызов с retry (requests - синхронная библиотека)
            embedding = await asyncio.to_thread(_call_with_retry, text)
            return embedding
        except Exception as e:
            logger.error("gigachat_embedding_failed", error=str(e), error_type=type(e).__name__)
            embedding_requests_total.labels(
                provider='gigachat',
                model=self.model,
                status='error'
            ).inc()
            raise
    
    def get_dimension(self) -> int:
        return self.dimension
    
    async def health_check(self) -> bool:
        try:
            test_embedding = await self.embed_text("test")
            return len(test_embedding) == self.dimension
        except Exception:
            return False

# ============================================================================
# EMBEDDING SERVICE
# ============================================================================

class EmbeddingService:
    """
    [C7-ID: AI-EMBEDDING-SERVICE-002] Унифицированный сервис генерации эмбеддингов.
    Только GigaChat API (без локальных моделей).
    """
    
    def __init__(self, primary_provider: EmbeddingProvider, fallback_provider: Optional[EmbeddingProvider] = None):
        self.primary_provider = primary_provider
        self.fallback_provider = fallback_provider
        
        logger.info("embedding_service_initialized",
                   primary_dim=primary_provider.get_dimension(),
                   has_fallback=fallback_provider is not None)
    
    async def generate_embedding(self, text: str, use_fallback: bool = False) -> List[float]:
        """
        Генерация эмбеддинга с автоматическим fallback.
        
        Args:
            text: Текст для эмбеддинга
            use_fallback: Принудительно использовать fallback провайдер
            
        Returns:
            Вектор эмбеддинга
        """
        if not text or not text.strip():
            raise ValueError("Empty text for embedding")
        
        provider = self.fallback_provider if use_fallback and self.fallback_provider else self.primary_provider
        
        try:
            embedding = await provider.embed_text(text)
            
            # Валидация размерности
            expected_dim = provider.get_dimension()
            if len(embedding) != expected_dim:
                raise ValueError(f"Dimension mismatch: got {len(embedding)}, expected {expected_dim}")
            
            return embedding
            
        except Exception as e:
            logger.error("embedding_generation_failed", provider=type(provider).__name__, error=str(e))
            
            # Fallback если доступен
            if not use_fallback and self.fallback_provider:
                logger.warning("embedding_fallback_attempt")
                return await self.generate_embedding(text, use_fallback=True)
            
            raise
    
    async def generate_embedding_or_zeros(self, text: str) -> List[float]:
        """
        Генерация эмбеддинга с fallback на нулевой вектор (для non-blocking pipeline).
        """
        try:
            return await self.generate_embedding(text)
        except Exception as e:
            logger.warning("embedding_fallback_zeros", error=str(e))
            return [0.0] * self.primary_provider.get_dimension()
    
    def get_dimension(self) -> int:
        """Размерность primary провайдера."""
        return self.primary_provider.get_dimension()
    
    async def health_check(self) -> Dict[str, Any]:
        """Health check всех провайдеров."""
        primary_healthy = await self.primary_provider.health_check()
        fallback_healthy = await self.fallback_provider.health_check() if self.fallback_provider else None
        
        return {
            'primary': {
                'healthy': primary_healthy,
                'dimension': self.primary_provider.get_dimension()
            },
            'fallback': {
                'healthy': fallback_healthy,
                'dimension': self.fallback_provider.get_dimension()
            } if self.fallback_provider else None
        }

# ============================================================================
# FACTORY
# ============================================================================

async def create_embedding_service(ai_adapter) -> EmbeddingService:
    """
    [C7-ID: AI-EMBEDDING-FACTORY-001] Создание EmbeddingService.
    
    Args:
        ai_adapter: Существующий GigaChainAdapter
        
    Returns:
        Настроенный EmbeddingService
    """
    # Используем GigaChat через gpt2giga proxy для embeddings
    primary = GigaChatEmbeddingProvider(ai_adapter)
    
    # Fallback на OpenRouter (если нужно)
    fallback = None  # Пока отключаем fallback
    
    return EmbeddingService(primary_provider=primary, fallback_provider=fallback)
