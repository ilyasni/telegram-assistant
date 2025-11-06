"""
Intent Classification Service
Context7 best practice: классификация намерений пользователя с использованием GigaChat через gpt2giga-proxy
"""

import hashlib
import json
from typing import Literal, Optional
from datetime import datetime, timezone

import structlog
from pydantic import BaseModel, Field
from redis import Redis
from langchain_gigachat import GigaChat
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser

from config import settings

logger = structlog.get_logger()

# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class IntentResponse(BaseModel):
    """Ответ классификатора намерений."""
    intent: Literal["ask", "search", "recommend", "trend", "digest"] = Field(..., description="Определенное намерение")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Уверенность классификации (0.0-1.0)")


# ============================================================================
# INTENT CLASSIFIER SERVICE
# ============================================================================

class IntentClassifier:
    """Сервис для классификации намерений пользователя."""
    
    # Context7: Few-shot промпт с примерами
    INTENT_CLASSIFICATION_PROMPT = ChatPromptTemplate.from_messages([
        ("system", """Ты — эксперт по определению намерений пользователей в запросах к информационной системе.

Определи намерение пользователя в запросе. Варианты:
- ask: конкретный вопрос, требующий ответа на основе контента каналов
- search: поиск информации по ключевым словам
- recommend: запрос рекомендаций похожего контента
- trend: запрос о трендах или популярных темах
- digest: запрос дайджеста новостей

Примеры:
"Что нового в AI?" → {{"intent": "ask", "confidence": 0.9}}
"Найди посты про машинное обучение" → {{"intent": "search", "confidence": 0.95}}
"Порекомендуй похожие новости" → {{"intent": "recommend", "confidence": 0.85}}
"Какие сейчас тренды?" → {{"intent": "trend", "confidence": 0.9}}
"Дай дайджест" → {{"intent": "digest", "confidence": 0.8}}

Запрос: {query}

Верни JSON: {{"intent": "...", "confidence": 0.0-1.0}}"""),
        ("human", "{query}")
    ])
    
    def __init__(
        self,
        redis_client: Optional[Redis] = None,
        openai_api_base: Optional[str] = None,
        openai_api_key: Optional[str] = None
    ):
        """
        Инициализация Intent Classifier.
        
        Args:
            redis_client: Redis клиент для кэширования
            openai_api_base: URL gpt2giga-proxy (по умолчанию из settings)
            openai_api_key: API ключ (по умолчанию из settings)
        """
        self.redis_client = redis_client
        
        # Инициализация GigaChat через langchain-gigachat
        # Context7: Используем gpt2giga-proxy как OpenAI-compatible endpoint
        # Context7: Исправлен URL (без /v1) для обработки редиректов прокси
        api_base = openai_api_base or settings.openai_api_base or "http://gpt2giga-proxy:8090"
        api_key = openai_api_key or settings.openai_api_key or "dummy"
        
        self.llm = GigaChat(
            credentials=settings.gigachat_credentials,
            scope=settings.gigachat_scope or "GIGACHAT_API_PERS",
            model="GigaChat",
            base_url=api_base,
            temperature=0.1,  # Низкая температура для более детерминированных результатов
        )
        
        # Pydantic Output Parser для валидации JSON ответа
        self.output_parser = PydanticOutputParser(pydantic_object=IntentResponse)
        
        # Добавляем инструкции по формату в промпт
        self.prompt = self.INTENT_CLASSIFICATION_PROMPT.partial(
            format_instructions=self.output_parser.get_format_instructions()
        )
        
        logger.info(
            "Intent Classifier initialized",
            api_base=api_base,
            model="GigaChat"
        )
    
    def _get_cache_key(self, user_id: str, query: str) -> str:
        """Генерация ключа кэша: sha1(user_id:query[:512])."""
        # Ограничиваем длину запроса до 512 символов для кэша
        query_truncated = query[:512]
        cache_data = f"{user_id}:{query_truncated}"
        cache_hash = hashlib.sha1(cache_data.encode()).hexdigest()
        return f"intent:cache:{cache_hash}"
    
    async def _get_from_cache(self, cache_key: str) -> Optional[IntentResponse]:
        """Получение результата из кэша."""
        if not self.redis_client:
            return None
        
        try:
            cached = self.redis_client.get(cache_key)
            if cached:
                data = json.loads(cached)
                result = IntentResponse(**data)
                logger.debug("Intent classification cache hit", cache_key=cache_key[:32])
                return result
        except Exception as e:
            logger.error("Error reading from cache", error=str(e), cache_key=cache_key[:32])
        
        return None
    
    async def _save_to_cache(self, cache_key: str, result: IntentResponse) -> None:
        """Сохранение результата в кэш."""
        if not self.redis_client:
            return
        
        try:
            # TTL 1 час
            data = result.model_dump()
            self.redis_client.setex(cache_key, 3600, json.dumps(data))
            logger.debug("Intent classification cache saved", cache_key=cache_key[:32], ttl=3600)
        except Exception as e:
            logger.error("Error saving to cache", error=str(e), cache_key=cache_key[:32])
    
    async def classify(
        self,
        query: str,
        user_id: Optional[str] = None
    ) -> IntentResponse:
        """
        Классификация намерения пользователя.
        
        Args:
            query: Текст запроса пользователя
            user_id: ID пользователя (для кэширования)
        
        Returns:
            IntentResponse с определенным намерением и уверенностью
        """
        # Проверка кэша
        if user_id:
            cache_key = self._get_cache_key(user_id, query)
            cached_result = await self._get_from_cache(cache_key)
            if cached_result:
                return cached_result
        
        try:
            # Формируем промпт
            formatted_prompt = self.prompt.format(query=query)
            
            # Вызываем LLM
            # Context7: Проверяем, что formatted_prompt - это объект промпта, а не строка
            if isinstance(formatted_prompt, str):
                logger.error("formatted_prompt is a string, not a prompt object", query=query[:50])
                # Fallback: используем дефолтный intent
                return IntentResponse(intent="search", confidence=0.2)
            
            # Context7: Получаем messages из промпта
            # ChatPromptTemplate.format() возвращает объект с методом format_messages()
            if hasattr(formatted_prompt, 'format_messages'):
                # Если это еще не форматированный промпт, форматируем его
                try:
                    messages = formatted_prompt.format_messages(query=query)
                except Exception:
                    # Если уже отформатирован, используем messages напрямую
                    messages = formatted_prompt.messages if hasattr(formatted_prompt, 'messages') else []
            elif hasattr(formatted_prompt, 'messages'):
                messages = formatted_prompt.messages
            else:
                logger.error("formatted_prompt has no messages attribute", query=query[:50], type=type(formatted_prompt).__name__)
                return IntentResponse(intent="search", confidence=0.2)
            
            if not messages:
                logger.error("Empty messages after formatting", query=query[:50])
                return IntentResponse(intent="search", confidence=0.2)
            
            response = await self.llm.ainvoke(messages)
            
            # Парсим ответ через Pydantic
            content = response.content if hasattr(response, 'content') else str(response)
            
            # Пытаемся извлечь JSON из ответа
            # GigaChat может вернуть JSON в markdown code block или просто текст
            json_match = None
            if "```json" in content:
                json_match = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                json_match = content.split("```")[1].split("```")[0].strip()
            elif "{" in content and "}" in content:
                # Ищем JSON в тексте
                start = content.find("{")
                end = content.rfind("}") + 1
                if start >= 0 and end > start:
                    json_match = content[start:end]
            else:
                json_match = content.strip()
            
            # Парсим JSON
            try:
                if json_match:
                    parsed_data = json.loads(json_match)
                else:
                    # Fallback: пытаемся найти intent в тексте
                    parsed_data = {"intent": "search", "confidence": 0.5}
                    for intent_type in ["ask", "search", "recommend", "trend", "digest"]:
                        if intent_type in content.lower():
                            parsed_data["intent"] = intent_type
                            break
                
                result = IntentResponse(**parsed_data)
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning("Failed to parse JSON response, using fallback", error=str(e), content=content[:100])
                # Fallback: если не удалось распарсить, используем search с низкой уверенностью
                result = IntentResponse(intent="search", confidence=0.3)
            
            # Порог уверенности: если confidence < 0.5 → degrade до search (без генерации)
            if result.confidence < 0.5:
                logger.debug("Low confidence, degrading to search", original_intent=result.intent, confidence=result.confidence)
                result = IntentResponse(intent="search", confidence=result.confidence)
            
            # Сохраняем в кэш
            if user_id:
                cache_key = self._get_cache_key(user_id, query)
                await self._save_to_cache(cache_key, result)
            
            logger.info(
                "Intent classified",
                query=query[:50],
                intent=result.intent,
                confidence=result.confidence
            )
            
            return result
        
        except Exception as e:
            logger.error("Error classifying intent", error=str(e), query=query[:50])
            # Fallback: возвращаем search с низкой уверенностью
            return IntentResponse(intent="search", confidence=0.2)


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_intent_classifier: Optional[IntentClassifier] = None


def get_intent_classifier(redis_client: Optional[Redis] = None) -> IntentClassifier:
    """Получение singleton экземпляра IntentClassifier."""
    global _intent_classifier
    if _intent_classifier is None:
        _intent_classifier = IntentClassifier(redis_client=redis_client)
    return _intent_classifier

