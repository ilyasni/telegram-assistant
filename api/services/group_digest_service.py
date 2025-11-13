"""
Group Digest Service — генерация дайджестов разговоров в группах.

Context7:
- Используем только облачные модели GigaChat через `langchain_gigachat`.
- Мультиагентный оркестратор реализован на LangGraph (`worker/tasks/group_digest_agent.py`).
- Сервис отвечает за выборку данных, запуск оркестра и сохранение результатов в Postgres.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import os
from typing import Any, Dict, List, Optional
from uuid import UUID

import structlog
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import and_

from models.database import (
    GroupConversationWindow,
    GroupDigest,
    GroupDigestMetric,
    GroupDigestParticipant,
    GroupDigestTopic,
    GroupMessage,
    User,
)
# Context7: импортируем оркестратор и event publisher как из API-контекста, так и из worker-контейнера
try:
    from worker.tasks.group_digest_agent import GroupDigestOrchestrator
    from worker.event_bus import EventPublisher, create_publisher
except ModuleNotFoundError:  # pragma: no cover - fallback для worker-контейнера
    from tasks.group_digest_agent import GroupDigestOrchestrator  # type: ignore
    from event_bus import EventPublisher, create_publisher  # type: ignore
from api.services.graph_service import get_graph_service

logger = structlog.get_logger()


class GroupDigestContent(BaseModel):
    digest_id: str
    window_id: str
    group_id: str
    content: str
    message_count: int
    topics: List[Dict[str, Any]]
    participants: List[Dict[str, Any]]
    metrics: Dict[str, Any]
    evaluation: Dict[str, Any]
    baseline_delta: Dict[str, Any] = Field(default_factory=dict)
    context_stats: Dict[str, Any] = Field(default_factory=dict)
    context_ranking: List[Dict[str, Any]] = Field(default_factory=list)
    context_duplicates: Dict[str, List[str]] = Field(default_factory=dict)
    context_history_links: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    context_media_highlights: List[Dict[str, Any]] = Field(default_factory=list)
    context_media_stats: Dict[str, Any] = Field(default_factory=dict)


class GroupDigestService:
    """Сервис генерации дайджестов для Telegram-групп."""

    def __init__(
        self,
        orchestrator: Optional[GroupDigestOrchestrator] = None,
        event_publisher: Optional[EventPublisher] = None,
        redis_url: Optional[str] = None,
    ):
        self._orchestrator = orchestrator or GroupDigestOrchestrator()
        self._graph_service = get_graph_service()
        self._event_publisher = event_publisher
        self._redis_url = redis_url or os.getenv("REDIS_URL", "redis://redis:6379")
        self._publisher_lock = asyncio.Lock()

    @staticmethod
    def _detect_media_kind(mime_type: Optional[str], legacy_hint: Optional[str]) -> str:
        """Определение типа медиа для дайджеста."""
        if mime_type:
            if mime_type.startswith("image/"):
                return "image"
            if mime_type.startswith("video/"):
                return "video"
            if mime_type.startswith("audio/"):
                return "audio"
            if mime_type in {"application/pdf", "application/msword"} or mime_type.startswith("application/"):
                return "document"
        if legacy_hint:
            prefix = legacy_hint.split(":", 1)[0]
            mapping = {
                "photo": "image",
                "video": "video",
                "document": "document",
                "voice": "audio",
                "audio": "audio",
            }
            return mapping.get(prefix, "unknown")
        return "unknown"

    def _serialize_group_message(self, message: GroupMessage) -> Dict[str, Any]:
        """Подготовка структуры сообщения с обогащением медиа и аналитикой."""
        analytics_payload: Dict[str, Any] = {}
        media_analysis_map: Dict[str, Dict[str, Any]] = {}
        metadata_payload: Dict[str, Any] = {}

        if message.analytics:
            metadata_payload = message.analytics.metadata_payload or {}
            media_block = metadata_payload.get("media") or {}
            if isinstance(media_block, list):
                media_analysis_map = {
                    str(entry.get("file_sha256") or entry.get("sha256") or entry.get("id")): entry
                    for entry in media_block
                    if isinstance(entry, dict)
                }
            elif isinstance(media_block, dict):
                media_analysis_map = {
                    str(key): value for key, value in media_block.items() if isinstance(value, dict)
                }

            analytics_payload = {
                "sentiment_score": message.analytics.sentiment_score,
                "emotions": message.analytics.emotions or {},
                "tags": message.analytics.tags or [],
                "entities": message.analytics.entities or [],
                "moderation_flags": message.analytics.moderation_flags or {},
                "analysed_at": message.analytics.analysed_at.isoformat() if message.analytics.analysed_at else None,
                "metadata": metadata_payload,
            }

        media_items: List[Dict[str, Any]] = []
        legacy_media = message.media_urls or []
        sorted_media = sorted(getattr(message, "media_map", []) or [], key=lambda link: link.position or 0)

        for idx, media_link in enumerate(sorted_media):
            meta = media_link.meta or {}
            media_object = getattr(media_link, "media_object", None)
            mime_type = meta.get("mime_type") or (media_object.mime if media_object else None)
            size_bytes = meta.get("size_bytes") or (media_object.size_bytes if media_object else None)
            size_int = int(size_bytes) if isinstance(size_bytes, (int, float)) else None

            analysis = media_analysis_map.get(media_link.file_sha256, {})
            description = (
                analysis.get("summary")
                or analysis.get("description")
                or analysis.get("caption")
                or meta.get("description")
            )
            labels = analysis.get("labels") or analysis.get("keywords") or []
            ocr_text = analysis.get("ocr_text") or analysis.get("transcript")

            if not description and isinstance(metadata_payload.get("vision_summary"), dict):
                description = metadata_payload["vision_summary"].get(media_link.file_sha256)
            if not description and isinstance(metadata_payload.get("vision_summary"), str):
                description = metadata_payload["vision_summary"]

            kind = self._detect_media_kind(mime_type, legacy_media[idx] if idx < len(legacy_media) else None)

            item: Dict[str, Any] = {
                "file_sha256": media_link.file_sha256,
                "position": int(media_link.position or 0),
                "kind": kind,
                "mime_type": mime_type,
                "size_bytes": size_int,
                "description": description,
            }
            if labels:
                item["labels"] = labels
            if ocr_text:
                item["ocr_text"] = ocr_text
            item["has_description"] = bool(description)
            item["has_labels"] = bool(labels)
            item["has_text"] = bool(ocr_text)

            media_items.append(item)

        return {
            "id": str(message.id),
            "group_id": str(message.group_id),
            "tenant_id": str(message.tenant_id),
            "posted_at": message.posted_at.isoformat() if message.posted_at else "",
            "sender_tg_id": message.sender_tg_id,
            "sender_username": message.sender_username,
            "content": message.content or "",
            "media_urls": legacy_media,
            "media": media_items,
            "has_media": bool(media_items),
            "analytics": analytics_payload,
            "is_service": message.is_service,
        }

    async def generate(
        self,
        tenant_id: str,
        group_window_id: UUID,
        db: Session,
        requested_by_user_id: Optional[UUID] = None,
        delivery_channel: str = "telegram",
        delivery_format: str = "telegram_html",
    ) -> GroupDigestContent:
        """Генерация дайджеста по окну обсуждения."""
        window: Optional[GroupConversationWindow] = (
            db.query(GroupConversationWindow)
            .filter(
                GroupConversationWindow.id == group_window_id,
                GroupConversationWindow.tenant_id == UUID(tenant_id),
            )
            .first()
        )

        if window is None:
            raise ValueError("Окно обсуждения не найдено или принадлежит другому арендатору")

        messages: List[GroupMessage] = (
            db.query(GroupMessage)
            .options(
                selectinload(GroupMessage.analytics),
                selectinload(GroupMessage.media_map).selectinload("media_object"),
            )
            .filter(
                GroupMessage.group_id == window.group_id,
                GroupMessage.posted_at >= window.window_start,
                GroupMessage.posted_at <= window.window_end,
            )
            .order_by(GroupMessage.posted_at.asc())
            .all()
        )

        if not messages:
            raise ValueError("В указанном окне нет сообщений — нечего анализировать")

        payload = {
            "window": {
                "window_id": str(window.id),
                "group_id": str(window.group_id),
                "tenant_id": str(window.tenant_id),
                "window_start": window.window_start.isoformat(),
                "window_end": window.window_end.isoformat(),
                "message_count": window.message_count,
                "participant_count": window.participant_count,
            },
            "messages": [
                self._serialize_group_message(message)
                for message in messages
            ],
        }

        orchestrator_state = await self._orchestrator.generate_async(payload)
        skip = orchestrator_state.get("skip", False)
        delivery_info = orchestrator_state.get("delivery", {}) or {}
        summary_text = orchestrator_state.get("summary_html") or orchestrator_state.get("summary", "")
        topics = [] if skip else orchestrator_state.get("topics", [])
        participants = [] if skip else orchestrator_state.get("participants", [])
        metrics = {} if skip else orchestrator_state.get("metrics", {})
        evaluation = {} if skip else orchestrator_state.get("evaluation", {})
        baseline_delta = orchestrator_state.get("baseline_delta", {})
        context_stats = orchestrator_state.get("context_stats", {})
        context_ranking = orchestrator_state.get("context_ranking", [])
        context_duplicates = orchestrator_state.get("context_duplicates", {})
        context_history_links = orchestrator_state.get("context_history_links", {})
        errors = orchestrator_state.get("errors", [])
        delivery_status = delivery_info.get("status", "pending")
        dlq_events = orchestrator_state.get("dlq_events") or []

        if dlq_events:
            await self._publish_dlq_events(dlq_events)

        # Сохраняем дайджест и связанные сущности
        digest = GroupDigest(
            window_id=window.id,
            requested_by_user_id=requested_by_user_id,
            delivery_channel=delivery_channel,
            format=delivery_format,
            summary=summary_text,
            payload={
                "topics": topics,
                "participants": participants,
                "metrics": metrics,
                "evaluation": evaluation,
                "errors": errors,
                "baseline_delta": baseline_delta,
                "context_stats": context_stats,
                "context_ranking": context_ranking,
                "context_duplicates": context_duplicates,
                "context_history_links": context_history_links,
                "context_media_highlights": orchestrator_state.get("media_highlights", []),
                "context_media_stats": orchestrator_state.get("media_stats", {}),
            },
            evaluation_scores=evaluation,
            delivery_status=delivery_status,
            delivery_metadata=delivery_info,
        )
        db.add(digest)
        db.flush()  # Получаем digest.id

        if topics:
            for topic in topics:
                db.add(
                    GroupDigestTopic(
                        digest_id=digest.id,
                        topic=topic.get("title") or topic.get("topic") or "Без названия",
                        priority=topic.get("priority", "medium"),
                        message_count=int(topic.get("msg_count") or len(topic.get("message_ids") or [])),
                        representative_messages=topic.get("threads") or topic.get("message_ids") or [],
                        keywords=topic.get("keywords") or [],
                        actions=topic.get("actions") or [],
                    )
                )

        if participants:
            for participant in participants:
                db.add(
                    GroupDigestParticipant(
                        digest_id=digest.id,
                        participant_tg_id=participant.get("telegram_id"),
                        participant_username=participant.get("username"),
                        role=participant.get("role", "participant"),
                        message_count=participant.get("message_count", 0),
                        contribution_summary=participant.get("summary", ""),
                    )
                )

        if metrics:
            db.add(
                GroupDigestMetric(
                    digest_id=digest.id,
                    sentiment=metrics.get("intensity") or metrics.get("sentiment"),
                    stress_index=metrics.get("stress"),
                    collaboration_index=metrics.get("collaboration"),
                    conflict_index=metrics.get("conflict"),
                    enthusiasm_index=metrics.get("enthusiasm"),
                    raw_scores=metrics,
                    evaluated_at=datetime.utcnow(),
                )
            )

        window.status = "completed"
        window.generated_at = datetime.utcnow()
        window.message_count = len(messages)
        window.participant_count = len({m.sender_tg_id for m in messages if m.sender_tg_id})

        db.commit()

        try:
            await self._push_to_graph(
                tenant_id=str(window.tenant_id),
                group_id=str(window.group_id),
                digest_id=str(digest.id),
                generated_at=digest.generated_at or datetime.utcnow(),
                window_size_hours=window.window_size_hours,
                topics=topics or [],
                participants=participants or [],
                metrics=metrics or {},
            )
        except Exception as graph_err:
            logger.warning(
                "Failed to upsert group conversation into Neo4j",
                digest_id=str(digest.id),
                error=str(graph_err),
            )

        return GroupDigestContent(
            digest_id=str(digest.id),
            window_id=str(window.id),
            group_id=str(window.group_id),
            content=summary_text,
            message_count=len(messages),
            topics=topics,
            participants=participants,
            metrics=metrics,
            evaluation=evaluation,
            baseline_delta=baseline_delta,
            context_stats=context_stats,
            context_ranking=context_ranking,
            context_duplicates=context_duplicates,
            context_history_links=context_history_links,
            context_media_highlights=orchestrator_state.get("media_highlights", []),
            context_media_stats=orchestrator_state.get("media_stats", {}),
        )

    async def _push_to_graph(
        self,
        tenant_id: str,
        group_id: str,
        digest_id: str,
        generated_at: datetime,
        window_size_hours: int,
        topics: List[Dict[str, Any]],
        participants: List[Dict[str, Any]],
        metrics: Dict[str, Any],
    ) -> None:
        """Отправка результатов дайджеста в Neo4j для построения профилей участников."""
        if not self._graph_service:
            return

        participants_payload: List[Dict[str, Any]] = []
        for participant in participants:
            raw_tg_id = participant.get("telegram_id")
            username = participant.get("username")
            if raw_tg_id is None and not username:
                continue
            participant_key = (
                f"tg:{raw_tg_id}"
                if raw_tg_id is not None
                else f"user:{username.lower()}"
            )
            participants_payload.append(
                {
                    "key": participant_key,
                    "telegram_id": raw_tg_id,
                    "username": username,
                    "role": participant.get("role", "participant"),
                    "message_count": participant.get("message_count", 0),
                    "summary": participant.get("summary", ""),
                }
            )

        await self._graph_service.upsert_group_conversation(
            tenant_id=tenant_id,
            group_id=group_id,
            digest_id=digest_id,
            generated_at=generated_at.isoformat(),
            window_size_hours=window_size_hours,
            topics=topics,
            participants=participants_payload,
            metrics=metrics,
        )


    async def _ensure_event_publisher(self) -> Optional[EventPublisher]:
        if self._event_publisher is not None:
            return self._event_publisher
        async with self._publisher_lock:
            if self._event_publisher is None:
                try:
                    self._event_publisher = await create_publisher(self._redis_url)
                except Exception as exc:
                    logger.warning("digest_dlq_publisher_init_failed", error=str(exc))
            return self._event_publisher

    async def _publish_dlq_events(self, events: List[Dict[str, Any]]) -> None:
        if not events:
            return
        publisher = await self._ensure_event_publisher()
        if publisher is None:
            logger.warning("digest_dlq_publisher_unavailable", events=len(events))
            return
        for event in events:
            try:
                await publisher.publish_json("digests.generate.dlq", event)
            except Exception as exc:
                logger.warning("digest_dlq_publish_failed", error=str(exc), event=event)


_group_digest_service: Optional[GroupDigestService] = None


def get_group_digest_service() -> GroupDigestService:
    """Singleton-провайдер GroupDigestService."""
    global _group_digest_service
    if _group_digest_service is None:
        _group_digest_service = GroupDigestService()
    return _group_digest_service

