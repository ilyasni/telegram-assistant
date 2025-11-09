"""Сервис персонализации триггеров для Crawl4ai."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence, Set

import structlog
from sqlalchemy.orm import Session

from api.config import settings
from models.database import User, UserCrawlTriggers

logger = structlog.get_logger()

DEFAULT_DIALOG_TTL_HOURS = int(os.getenv("CRAWL_TRIGGER_DIALOG_TTL_HOURS", "72"))
MAX_DIALOG_TOPICS = int(os.getenv("CRAWL_TRIGGER_DIALOG_MAX", "20"))
MAX_TRIGGERS = int(os.getenv("CRAWL_TRIGGER_MAX_TOTAL", "60"))

_WORD_RE = re.compile(r"[\\w\\-+#]{3,}", re.IGNORECASE | re.UNICODE)


def _normalize_single(value: str) -> Optional[str]:
    if not value:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    cleaned = cleaned.replace("_", " ").replace("ё", "е")
    cleaned = re.sub(r"\\s+", " ", cleaned)
    return cleaned.lower()


def _deduplicate(values: Sequence[str]) -> List[str]:
    seen: Set[str] = set()
    ordered: List[str] = []
    for value in values:
        if not value:
            continue
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def _expand_topics_with_gigachat(topics: Sequence[str]) -> List[str]:
    normalized = [t for t in (_normalize_single(topic) for topic in topics) if t]
    if not normalized:
        return []

    credentials = getattr(settings, "gigachat_credentials", None)
    if not credentials:
        return []

    try:
        from langchain_gigachat import GigaChat
        prompt = (
            "Сгенерируй расширенный список тегов и ключевых слов по темам пользователя.\n"
            "Темы: {topics}\n"
            "Верни только список ключевых слов через запятую, без пояснений, в нижнем регистре."
        )

        credentials_value = credentials.get_secret_value() if hasattr(credentials, "get_secret_value") else str(credentials)
        scope_value = getattr(settings, "gigachat_scope", "GIGACHAT_API_PERS")
        if hasattr(scope_value, "get_secret_value"):
            scope_value = scope_value.get_secret_value()

        base_url = getattr(settings, "openai_api_base", None) or os.getenv("OPENAI_API_BASE", "http://gpt2giga-proxy:8090")
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]

        client = GigaChat(
            credentials=credentials_value,
            scope=scope_value,
            base_url=base_url,
            verify_ssl_certs=False,
            temperature=0.2,
        )
        answer = client.invoke(prompt.format(topics=", ".join(normalized)))
        text = getattr(answer, "content", None) or getattr(answer, "text", None) or str(answer)
        if not text:
            return []
        candidates = [token.strip().lower() for token in text.split(",")]
        return [token for token in candidates if token]
    except Exception as exc:  # noqa: BLE001
        logger.warning("gigachat_expand_failed", error=str(exc))
        return []


def _heuristic_expand(topics: Sequence[str]) -> List[str]:
    tokens: List[str] = []
    for topic in topics:
        normalized = _normalize_single(topic)
        if not normalized:
            continue
        tokens.append(normalized)
        parts = re.split(r"[\\s,/|]+", normalized)
        for part in parts:
            if len(part) >= 3:
                tokens.append(part)
    return _deduplicate(tokens)


def _merge_dialog_topics(existing: List[str], new_topics: Sequence[str]) -> List[str]:
    normalized_existing = [_normalize_single(t) for t in existing if t]
    normalized_new = [_normalize_single(t) for t in new_topics if t]
    merged = _deduplicate([t for t in normalized_new if t] + [t for t in normalized_existing if t])
    return merged[:MAX_DIALOG_TOPICS]


def _calculate_effective_triggers(
    base_topics: Sequence[str],
    dialog_topics: Sequence[str],
    derived_keywords: Sequence[str],
) -> List[str]:
    merged = _deduplicate(
        list(_normalize_single(t) for t in base_topics if t)
        + list(_normalize_single(t) for t in dialog_topics if t)
        + list(_normalize_single(t) for t in derived_keywords if t)
    )
    return merged[:MAX_TRIGGERS]


def _dialog_topics_valid(updated_at: Optional[datetime]) -> bool:
    if not updated_at:
        return False
    now = datetime.now(timezone.utc)
    return now - updated_at <= timedelta(hours=DEFAULT_DIALOG_TTL_HOURS)


def upsert_triggers_from_digest(
    db: Session,
    user: User,
    topics: Sequence[str],
) -> UserCrawlTriggers:
    """Обновить триггеры на основе настроек дайджеста."""
    base_topics = [topic for topic in topics if topic]
    derived = _deduplicate(_expand_topics_with_gigachat(base_topics) + _heuristic_expand(base_topics))

    record = db.query(UserCrawlTriggers).filter(UserCrawlTriggers.user_id == user.id).first()
    if record is None:
        record = UserCrawlTriggers(
            user_id=user.id,
            tenant_id=user.tenant_id,
            base_topics=base_topics,
            derived_keywords=derived,
            triggers=_calculate_effective_triggers(base_topics, [], derived),
            metadata_payload={"source": "digest_settings"},
            dialog_topics_updated_at=datetime.now(timezone.utc),
        )
        db.add(record)
    else:
        dialog_payload: Sequence[str] = []
        if _dialog_topics_valid(record.dialog_topics_updated_at):
            dialog_payload = record.dialog_topics or []

        record.base_topics = base_topics
        record.derived_keywords = derived
        record.triggers = _calculate_effective_triggers(base_topics, dialog_payload, derived)
        record.metadata_payload = {**(record.metadata_payload or {}), "source": "digest_settings"}
        record.tenant_id = user.tenant_id

    record.updated_at = datetime.now(timezone.utc)
    db.flush()
    return record


def update_triggers_from_dialog(
    db: Session,
    user: User,
    query_text: str,
    intent: Optional[str] = None,
) -> Optional[UserCrawlTriggers]:
    """Обновить триггеры по мотивам вопросов пользователя."""
    extracted = extract_keywords_from_query(query_text, intent)
    if not extracted:
        return None

    record = db.query(UserCrawlTriggers).filter(UserCrawlTriggers.user_id == user.id).with_for_update(of=UserCrawlTriggers).first()
    if record is None:
        record = UserCrawlTriggers(
            user_id=user.id,
            tenant_id=user.tenant_id,
            base_topics=[],
            dialog_topics=_deduplicate(extracted)[:MAX_DIALOG_TOPICS],
            derived_keywords=[],
            triggers=_deduplicate(extracted)[:MAX_TRIGGERS],
            metadata_payload={"source": "dialog"},
            dialog_topics_updated_at=datetime.now(timezone.utc),
        )
        db.add(record)
    else:
        dialog_topics = record.dialog_topics or []
        if not _dialog_topics_valid(record.dialog_topics_updated_at):
            dialog_topics = []

        record.dialog_topics = _merge_dialog_topics(dialog_topics, extracted)
        record.dialog_topics_updated_at = datetime.now(timezone.utc)
        record.triggers = _calculate_effective_triggers(record.base_topics or [], record.dialog_topics or [], record.derived_keywords or [])
        now_iso = datetime.now(timezone.utc).isoformat()
        record.metadata_payload = {**(record.metadata_payload or {}), "dialog_intent": intent, "dialog_updated_at": now_iso}

    record.updated_at = datetime.now(timezone.utc)
    db.flush()
    return record


def extract_keywords_from_query(query_text: str, intent: Optional[str] = None) -> List[str]:
    """Выделить ключевые слова из запроса пользователя."""
    if not query_text:
        return []

    keywords: List[str] = []
    for match in _WORD_RE.finditer(query_text):
        token = match.group().lower()
        if token and len(token) >= 3:
            keywords.append(token)

    if intent:
        intent_norm = _normalize_single(intent)
        if intent_norm:
            keywords.append(intent_norm)

    return _deduplicate(keywords)[:MAX_TRIGGERS]

