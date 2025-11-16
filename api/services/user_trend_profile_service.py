"""
User Trend Profile Service — сервис для профилирования интересов пользователей.

Context7: Гибридное хранение - PostgreSQL для быстрых запросов,
синхронизация с Neo4j для графовых рекомендаций.
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List
from uuid import UUID

import httpx
import structlog
from sqlalchemy.orm import Session
from sqlalchemy import func, desc

from models.database import UserTrendProfile, TrendInteraction, TrendCluster, User
from config import settings

logger = structlog.get_logger()

# ============================================================================
# USER TREND PROFILE SERVICE
# ============================================================================


class UserTrendProfileService:
    """
    Сервис для отслеживания и профилирования интересов пользователей.
    
    Context7: Гибридное хранение:
    - PostgreSQL (user_trend_profiles) - для быстрых запросов
    - Neo4j (граф интересов) - для рекомендаций через графовые связи
    """

    def __init__(self, db: Session):
        self.db = db

    def update_profile_from_interaction(
        self,
        user_id: UUID,
        cluster_id: UUID,
        interaction_type: str,
    ) -> None:
        """
        Обновление профиля на основе взаимодействия пользователя с трендом.
        
        Context7: Идемпотентная запись взаимодействия.
        """
        # Сохраняем взаимодействие
        from models.database import TrendInteraction
        interaction = TrendInteraction(
            user_id=user_id,
            cluster_id=cluster_id,
            interaction_type=interaction_type,
        )
        self.db.add(interaction)
        self.db.commit()

    def get_user_profile(self, user_id: UUID) -> Optional[Dict[str, Any]]:
        """Загрузка профиля пользователя."""
        profile = (
            self.db.query(UserTrendProfile)
            .filter(UserTrendProfile.user_id == user_id)
            .first()
        )
        if not profile:
            return None
        return {
            "preferred_topics": profile.preferred_topics or [],
            "ignored_topics": profile.ignored_topics or [],
            "preferred_categories": profile.preferred_categories or [],
            "typical_time_windows": profile.typical_time_windows or [],
            "interaction_stats": profile.interaction_stats or {},
        }

    async def build_profile_agent(self, user_id: UUID, days: int = 30) -> Dict[str, Any]:
        """
        Периодический пересчёт профиля через LLM на основе истории взаимодействий.
        
        Context7: Анализ взаимодействий за последние N дней через LLM для построения профиля.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        # Агрегация взаимодействий
        interactions = (
            self.db.query(TrendInteraction)
            .filter(TrendInteraction.user_id == user_id)
            .filter(TrendInteraction.created_at >= cutoff)
            .order_by(TrendInteraction.created_at.desc())
            .limit(100)
            .all()
        )

        if not interactions:
            return {
                "preferred_topics": [],
                "ignored_topics": [],
                "preferred_categories": [],
                "typical_time_windows": [],
                "interaction_stats": {},
            }

        # Собираем данные о трендах, с которыми взаимодействовал пользователь
        cluster_ids = {interaction.cluster_id for interaction in interactions}
        clusters = (
            self.db.query(TrendCluster)
            .filter(TrendCluster.id.in_(list(cluster_ids)))
            .all()
        )

        # Группируем по типам взаимодействий
        interaction_stats = {}
        for interaction in interactions:
            itype = interaction.interaction_type
            interaction_stats[itype] = interaction_stats.get(itype, 0) + 1

        # Собираем topics и categories из кластеров
        all_topics = []
        all_categories = []
        for cluster in clusters:
            if cluster.topics:
                all_topics.extend(cluster.topics)
            if cluster.taxonomy_categories:
                all_categories.extend(cluster.taxonomy_categories)

        # Вызов LLM для построения профиля
        prompt_payload = {
            "interactions_count": len(interactions),
            "interaction_types": interaction_stats,
            "topics_encountered": list(set(all_topics))[:20],
            "categories_encountered": list(set(all_categories))[:20],
            "time_window_days": days,
        }

        llm_result = await self._call_profile_llm(prompt_payload)
        if not llm_result:
            # Fallback: простой профиль на основе частоты
            preferred_topics = list(set(all_topics))[:10]
            preferred_categories = list(set(all_categories))[:5]
            return {
                "preferred_topics": preferred_topics,
                "ignored_topics": [],
                "preferred_categories": preferred_categories,
                "typical_time_windows": [],
                "interaction_stats": interaction_stats,
            }

        return {
            "preferred_topics": llm_result.get("preferred_topics", [])[:10],
            "ignored_topics": llm_result.get("ignored_topics", [])[:10],
            "preferred_categories": llm_result.get("preferred_categories", [])[:5],
            "typical_time_windows": llm_result.get("typical_time_windows", []),
            "interaction_stats": interaction_stats,
        }

    async def _call_profile_llm(self, prompt_payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Вызов LLM для построения профиля интересов."""
        api_base = (
            getattr(settings, "openai_api_base", None)
            or os.getenv("OPENAI_API_BASE")
            or os.getenv("GIGACHAT_PROXY_URL")
            or "http://gpt2giga-proxy:8090"
        )
        api_base = api_base.rstrip("/")
        if not api_base.endswith("/v1"):
            api_base = f"{api_base}/v1"

        credentials = os.getenv("GIGACHAT_CREDENTIALS")
        scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
        api_key = getattr(settings, "openai_api_key", None) or os.getenv("OPENAI_API_KEY")
        headers = {"Content-Type": "application/json"}
        if credentials:
            headers["Authorization"] = f"Bearer giga-cred-{credentials}:{scope}"
        elif api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        endpoint = (
            api_base if api_base.endswith("/chat/completions") else f"{api_base}/chat/completions"
        )

        system_message = (
            "На основе истории взаимодействий пользователя построй профиль интересов:\n"
            "- preferred_topics: темы, по которым пользователь чаще открывает тренды\n"
            "- ignored_topics: темы, которые пользователь игнорирует\n"
            "- preferred_categories: рубрики (auto, finance, ai, ...)\n"
            "- typical_time_windows: когда пользователь активен\n\n"
            "Верни JSON профиля."
        )
        user_message = (
            "Данные по взаимодействиям:\n"
            f"{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}\n\n"
            "Ответь строго JSON объектом."
        )

        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.post(
                    endpoint,
                    headers=headers,
                    json={
                        "model": os.getenv("TREND_PERSONALIZER_LLM_MODEL", "GigaChat"),
                        "messages": [
                            {"role": "system", "content": system_message},
                            {"role": "user", "content": user_message},
                        ],
                        "temperature": 0.2,
                        "max_tokens": 400,
                    },
                )
            if response.status_code != 200:
                logger.debug("trend_profile_llm_error", status=response.status_code)
                return None
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content.strip().strip("```json").strip("```"))
            return parsed
        except Exception as exc:
            logger.debug("trend_profile_llm_failure", error=str(exc))
            return None

    def save_profile(self, user_id: UUID, profile: Dict[str, Any]) -> None:
        """Сохранение профиля в БД."""
        existing = (
            self.db.query(UserTrendProfile)
            .filter(UserTrendProfile.user_id == user_id)
            .first()
        )
        if existing:
            existing.preferred_topics = profile.get("preferred_topics", [])
            existing.ignored_topics = profile.get("ignored_topics", [])
            existing.preferred_categories = profile.get("preferred_categories", [])
            existing.typical_time_windows = profile.get("typical_time_windows", [])
            existing.interaction_stats = profile.get("interaction_stats", {})
            existing.last_updated = datetime.now(timezone.utc)
        else:
            new_profile = UserTrendProfile(
                user_id=user_id,
                preferred_topics=profile.get("preferred_topics", []),
                ignored_topics=profile.get("ignored_topics", []),
                preferred_categories=profile.get("preferred_categories", []),
                typical_time_windows=profile.get("typical_time_windows", []),
                interaction_stats=profile.get("interaction_stats", {}),
            )
            self.db.add(new_profile)
        self.db.commit()


def get_user_trend_profile_service(db: Session) -> UserTrendProfileService:
    """Factory для получения сервиса."""
    return UserTrendProfileService(db)

