"""
Persona Service - сервис для персонализации через embedding profiles и адаптацию промптов.

Performance guardrails:
- Embeddings персон и предпочтений считаются офлайн: на батчах истории раз в N часов/дней
- Per-request personalization: вшивать в промпт полегчённое summary профиля, не весь профиль (до 100-200 токенов)
"""

from __future__ import annotations

from typing import Dict, List, Optional, Any
from uuid import UUID

import structlog

logger = structlog.get_logger(__name__)


class PersonaService:
    """Сервис для работы с персонами и персонализацией."""
    
    def __init__(self):
        self._max_profile_tokens = 200  # Максимум токенов для профиля в промпте
    
    def get_persona_profile_summary(
        self,
        user_id: UUID | str,
        tenant_id: UUID | str,
    ) -> str:
        """
        Получить краткое summary профиля персоны для вставки в промпт.
        
        Performance: До 100-200 токенов, не весь профиль.
        
        Args:
            user_id: ID пользователя
            tenant_id: ID тенанта
        
        Returns:
            Краткое summary профиля
        """
        # TODO: Реализация получения summary профиля
        # Placeholder
        return "User preferences: general topics"
    
    def adapt_prompt(
        self,
        base_prompt: str,
        persona_summary: str,
    ) -> str:
        """
        Адаптировать промпт под персону.
        
        Args:
            base_prompt: Базовый промпт
            persona_summary: Краткое summary персоны
        
        Returns:
            Адаптированный промпт
        """
        # Ограничение длины summary
        if len(persona_summary) > self._max_profile_tokens * 4:  # Примерно 4 символа на токен
            persona_summary = persona_summary[:self._max_profile_tokens * 4]
        
        adapted = f"""{base_prompt}

Персона пользователя:
{persona_summary}
"""
        return adapted
    
    def update_persona_embeddings_offline(
        self,
        user_id: UUID | str,
        tenant_id: UUID | str,
    ) -> None:
        """
        Обновить embeddings персоны офлайн (на батчах истории).
        
        Performance: Выполняется периодически (раз в N часов/дней), не на каждый запрос.
        
        Args:
            user_id: ID пользователя
            tenant_id: ID тенанта
        """
        # TODO: Реализация офлайн обновления embeddings
        logger.info(
            "persona.embeddings_update_scheduled",
            user_id=str(user_id),
            tenant_id=str(tenant_id)
        )

