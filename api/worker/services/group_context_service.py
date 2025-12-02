from __future__ import annotations

import math
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import structlog

logger = structlog.get_logger(__name__)

PHONE_PATTERN = re.compile(r"(\+?\d[\d\s\-().]{6,})")
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
TOKEN_REGEX = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9]{2,}", re.UNICODE)
STOPWORDS = {
    "это",
    "или",
    "как",
    "что",
    "так",
    "если",
    "где",
    "нет",
    "для",
    "про",
    "при",
    "когда",
    "через",
    "уже",
    "после",
    "будет",
    "вас",
    "они",
    "все",
    "еще",
    "есть",
    "эта",
    "этот",
    "эти",
    "with",
    "from",
    "have",
    "just",
    "also",
    "about",
    "http",
    "https",
}


def _mask_phone_value(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) < 6:
        return "+***"
    prefix = digits[:2]
    suffix = digits[-2:]
    return f"+{prefix}***{suffix}"


def mask_pii(text: Optional[str]) -> str:
    """Маскирует телефоны / email в тексте."""
    if not text:
        return ""
    masked = PHONE_PATTERN.sub(lambda m: _mask_phone_value(m.group(0)), text)
    masked = EMAIL_PATTERN.sub("[email masked]", masked)
    return masked


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return min(upper, max(lower, value))


def parse_timestamp(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_timestamp(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def text_vector(text: str) -> Tuple[Counter, float]:
    tokens = TOKEN_REGEX.findall(text.lower())
    counter = Counter(tokens)
    norm = math.sqrt(sum(v * v for v in counter.values())) or 1.0
    return counter, norm


_TEXT_VECTOR_CACHE: Dict[str, Tuple[Counter, float]] = {}


def text_vector_cached(text: str) -> Tuple[Counter, float]:
    cache_key = text[:512]
    cached = _TEXT_VECTOR_CACHE.get(cache_key)
    if cached is not None:
        return cached
    vector = text_vector(text)
    if len(_TEXT_VECTOR_CACHE) > 2048:
        _TEXT_VECTOR_CACHE.clear()
    _TEXT_VECTOR_CACHE[cache_key] = vector
    return vector


def cosine_similarity(a: Tuple[Counter, float], b: Tuple[Counter, float]) -> float:
    vec_a, norm_a = a
    vec_b, norm_b = b
    common = set(vec_a) & set(vec_b)
    dot = sum(vec_a[token] * vec_b[token] for token in common)
    denom = norm_a * norm_b
    if denom == 0:
        return 0.0
    return dot / denom


def text_similarity(left: str, right: str) -> float:
    return cosine_similarity(text_vector_cached(left), text_vector_cached(right))


def build_participant_stats(messages: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    stats: Dict[str, Dict[str, Any]] = {}
    for msg in messages:
        username = msg.get("username") or f"user-{msg.get('telegram_id') or 'unknown'}"
        entry = stats.setdefault(
            username,
            {
                "username": username,
                "telegram_id": msg.get("telegram_id"),
                "message_count": 0,
                "media_count": 0,
                "media_types": {},
                "media_samples": [],
            },
        )
        entry["message_count"] += 1
        media_items = msg.get("media") or []
        if media_items:
            entry["media_count"] += len(media_items)
            media_types = entry.setdefault("media_types", {})
            for media in media_items:
                kind = media.get("kind") or "unknown"
                media_types[kind] = media_types.get(kind, 0) + 1
                if len(entry["media_samples"]) < 5:
                    sample = media.get("description") or media.get("ocr_excerpt")
                    if sample:
                        entry["media_samples"].append(sample[:160])
    return sorted(stats.values(), key=lambda item: item["message_count"], reverse=True)


def build_conversation_excerpt(messages: Sequence[Dict[str, Any]], limit: int = 20) -> str:
    subset = list(messages)[-limit:]
    lines = []
    for item in subset:
        lines.append(f"[{item['timestamp_iso']}] {item['username']}: {item['content']}")
    return "\n".join(lines)


def _tokenize(text: str) -> List[str]:
    return [
        token
        for token in TOKEN_REGEX.findall(text.lower())
        if len(token) > 3 and token not in STOPWORDS
    ]


def extract_reply_to(raw: Dict[str, Any]) -> Optional[str]:
    reply = raw.get("reply_to")
    if isinstance(reply, dict):
        for key in ("message_id", "tg_message_id", "id"):
            value = reply.get(key)
            if value:
                return str(value)
    for fallback_key in ("reply_to_message_id", "reply_to_id", "reply_to"):
        value = raw.get(fallback_key)
        if value:
            return str(value)
    return None


@dataclass
class ContextScoringWeights:
    recency: float = 0.5
    reply: float = 0.25
    length: float = 0.15
    reactions: float = 0.1
    media: float = 0.1


@dataclass
class ContextConfig:
    similarity_threshold: float = 0.88
    soft_similarity_threshold: float = 0.76
    dedup_time_gap_minutes: int = 120
    max_context_messages: int = 400
    top_ranked: int = 150
    recency_half_life_minutes: int = 180
    scoring: ContextScoringWeights = field(default_factory=ContextScoringWeights)


@dataclass
class ContextAssemblyResult:
    sanitized_messages: List[Dict[str, Any]]
    participant_stats: List[Dict[str, Any]]
    conversation_excerpt: str
    ranking: List[Dict[str, Any]]
    duplicates: Dict[str, List[str]]
    historical_links: Dict[str, Dict[str, Any]]
    stats: Dict[str, Any]
    sample_messages: List[Dict[str, Any]]
    media_highlights: List[Dict[str, Any]] = field(default_factory=list)
    media_stats: Dict[str, Any] = field(default_factory=dict)


class GroupContextService:
    """
    [C7-ID: DIGEST-CONTEXT-001] Сервис сбора контекста для мультиагентного пайплайна:
    - Санитизация и маскирование PII.
    - Дедупликация сообщений с учётом временной и семантической близости.
    - Ранжирование по эвристическому скорингу (recency, replies, длина, реакции).
    """

    def __init__(self, config: ContextConfig) -> None:
        self.config = config

    @staticmethod
    def _infer_media_kind(media_item: Dict[str, Any], fallback_hint: Optional[str] = None) -> str:
        mime_type = media_item.get("mime_type")
        if isinstance(mime_type, str):
            if mime_type.startswith("image/"):
                return "image"
            if mime_type.startswith("video/"):
                return "video"
            if mime_type.startswith("audio/"):
                return "audio"
            if mime_type.startswith("application/"):
                return "document"
        if fallback_hint:
            prefix = fallback_hint.split(":", 1)[0]
            mapping = {
                "photo": "image",
                "video": "video",
                "voice": "audio",
                "audio": "audio",
                "document": "document",
            }
            return mapping.get(prefix, "unknown")
        return "unknown"

    def _normalize_media_entries(
        self, raw: Dict[str, Any]
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        normalized: List[Dict[str, Any]] = []
        summaries: List[str] = []

        raw_media: List[Any] = []
        if isinstance(raw.get("media"), list):
            raw_media = [item for item in raw["media"] if isinstance(item, dict)]
        elif isinstance(raw.get("media_urls"), list):
            raw_media = [{"legacy_hint": hint} for hint in raw["media_urls"] if isinstance(hint, str)]

        if not raw_media:
            return normalized, summaries

        analytics = raw.get("analytics") or {}
        metadata = analytics.get("metadata") if isinstance(analytics, dict) else {}
        media_analysis_map: Dict[str, Dict[str, Any]] = {}
        if isinstance(metadata, dict):
            media_block = metadata.get("media") or {}
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

        for idx, media_item in enumerate(raw_media):
            file_id = str(
                media_item.get("file_sha256")
                or media_item.get("sha256")
                or media_item.get("id")
                or media_item.get("legacy_hint")
                or idx
            )

            analysis = media_analysis_map.get(file_id, {})

            kind = media_item.get("kind") or analysis.get("kind")
            if not kind:
                legacy_hint = media_item.get("legacy_hint")
                kind = self._infer_media_kind(media_item, legacy_hint)

            description = media_item.get("description") or analysis.get("summary") or analysis.get("description")
            labels = media_item.get("labels") or analysis.get("labels") or analysis.get("keywords") or []
            ocr_text = media_item.get("ocr_text") or analysis.get("ocr_text") or analysis.get("transcript")

            if description:
                description = mask_pii(str(description))[:160]
            if not description and labels:
                description = mask_pii(", ".join(str(label) for label in labels[:3]))[:160]
            if not description and ocr_text:
                description = mask_pii(str(ocr_text))[:160]
            if not description:
                description = f"{kind.capitalize()} без подписи"

            normalized_entry: Dict[str, Any] = {
                "id": file_id,
                "kind": kind,
                "description": description,
                "labels": [mask_pii(str(label)) for label in labels[:5]],
            }
            if ocr_text:
                normalized_entry["ocr_excerpt"] = mask_pii(str(ocr_text))[:200]
            if media_item.get("mime_type"):
                normalized_entry["mime_type"] = media_item["mime_type"]
            if media_item.get("size_bytes"):
                normalized_entry["size_bytes"] = media_item["size_bytes"]

            normalized.append(normalized_entry)
            summaries.append(f"{kind}: {description}")

        return normalized, summaries

    def assemble(
        self,
        *,
        window: Dict[str, Any],
        raw_messages: Sequence[Dict[str, Any]],
        tenant_id: str,
        trace_id: str,
        max_messages: int,
        excerpt_limit: int = 20,
        historical_messages: Optional[Sequence[Dict[str, Any]]] = None,
        historical_ranking: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> ContextAssemblyResult:
        sanitized = self._sanitize_messages(raw_messages)
        original_total = len(sanitized)

        historical_sanitized = self._normalize_historical_messages(historical_messages)
        deduped, duplicates, soft_links, historical_links = self._deduplicate_messages(
            sanitized, historical_sanitized
        )

        trimmed_for_limit = 0
        if len(deduped) > max_messages:
            deduped = self._select_top_messages(deduped, max_messages)
            trimmed_for_limit = len(sanitized) - len(deduped)

        ranking = self._rank_messages(deduped, soft_links, historical_links, historical_ranking or [])
        ranking_limited = ranking[: self.config.top_ranked]
        scores = {item["message_id"]: item["score"] for item in ranking_limited}
        for msg in deduped:
            msg["context_score"] = round(scores.get(msg["message_id"], 0.0), 4)

        participant_stats = build_participant_stats(deduped)

        media_total = 0
        media_messages = 0
        media_with_description = 0
        media_kinds: Counter[str] = Counter()
        media_highlights: List[Dict[str, Any]] = []
        for msg in deduped:
            media_entries = msg.get("media") or []
            if media_entries:
                media_messages += 1
            for media in media_entries:
                media_total += 1
                media_kinds[media.get("kind", "unknown")] += 1
                if media.get("description"):
                    media_with_description += 1
                highlight = {
                    "message_id": msg["message_id"],
                    "username": msg["username"],
                    "timestamp_iso": msg["timestamp_iso"],
                    "kind": media.get("kind"),
                    "description": media.get("description"),
                    "labels": media.get("labels", []),
                    "ocr_excerpt": media.get("ocr_excerpt"),
                    "score": msg.get("context_score", 0.0),
                }
                media_highlights.append(highlight)

        media_highlights = sorted(
            media_highlights,
            key=lambda item: (item.get("score", 0.0), item.get("timestamp_iso", "")),
            reverse=True,
        )[: min(6, len(media_highlights))]

        media_stats = {
            "media_total": media_total,
            "media_messages": media_messages,
            "media_with_description": media_with_description,
            "media_without_description": max(0, media_total - media_with_description),
            "media_kinds": dict(media_kinds),
        }

        excerpt_messages = self._excerpt_messages(ranking_limited, deduped, excerpt_limit)
        conversation_excerpt = build_conversation_excerpt(excerpt_messages, limit=excerpt_limit)

        stats = {
            "tenant_id": tenant_id,
            "window_id": window.get("window_id"),
            "original_messages": original_total,
            "deduplicated_messages": len(deduped),
            "duplicates_removed": sum(len(v) for v in duplicates.values()),
            "trimmed_for_max": trimmed_for_limit,
            "similarity_threshold": self.config.similarity_threshold,
            "soft_similarity_threshold": self.config.soft_similarity_threshold,
            "top_ranked": len(ranking_limited),
            "historical_messages": len(historical_sanitized),
            "historical_matches": len(historical_links),
        }
        stats.update(media_stats)

        logger.debug(
            "group_context_service.assembled",
            tenant_id=tenant_id,
            trace_id=trace_id,
            window_id=window.get("window_id"),
            stats=stats,
        )

        return ContextAssemblyResult(
            sanitized_messages=deduped,
            participant_stats=participant_stats,
            conversation_excerpt=conversation_excerpt,
            ranking=ranking_limited,
            duplicates=duplicates,
            historical_links=historical_links,
            stats=stats,
            sample_messages=deduped[: min(5, len(deduped))],
            media_highlights=media_highlights,
            media_stats=media_stats,
        )

    def build_keyword_topics(
        self,
        messages: Sequence[Dict[str, Any]],
        highlights: Sequence[Dict[str, Any]],
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        if not messages:
            return []

        message_count = len(messages)
        # Динамический порог частоты
        if message_count <= 10:
            min_freq = 1
        elif message_count <= 30:
            min_freq = 2
        else:
            min_freq = 3

        # Собираем униграммы и биграммы
        tokens_by_msg: List[Tuple[Dict[str, Any], List[str]]] = []
        unigram_counter: Counter[str] = Counter()
        bigram_counter: Counter[str] = Counter()
        
        for msg in messages:
            media_summary = msg.get('media_summary', [])
            if not isinstance(media_summary, list):
                media_summary = []
            content = f"{msg.get('content', '')} {' '.join(media_summary)}"
            tokens = _tokenize(content)
            if not tokens:
                continue
            tokens_by_msg.append((msg, tokens))
            unigram_counter.update(tokens)
            
            # Собираем биграммы
            for i in range(len(tokens) - 1):
                bigram = f"{tokens[i]} {tokens[i+1]}"
                bigram_counter[bigram] += 1

        if not unigram_counter and not bigram_counter:
            return []

        # Объединяем униграммы и биграммы, приоритет биграммам
        combined_items: List[Tuple[str, int]] = []
        for bigram, freq in bigram_counter.most_common(20):
            if freq >= min_freq:
                combined_items.append((bigram, freq * 2))  # Увеличиваем вес биграмм
        
        for unigram, freq in unigram_counter.most_common(20):
            if freq >= min_freq:
                # Проверяем, что униграмма не входит в уже выбранные биграммы
                if not any(unigram in item[0] for item in combined_items[:limit]):
                    combined_items.append((unigram, freq))
        
        # Сортируем по частоте и берём top-N
        combined_items.sort(key=lambda x: x[1], reverse=True)
        top_phrases = [phrase for phrase, _ in combined_items[:limit]]
        
        if not top_phrases:
            # Если ничего не нашлось, берём хотя бы самые частые
            if bigram_counter:
                top_phrases = [bigram_counter.most_common(1)[0][0]]
            elif unigram_counter:
                top_phrases = [unigram_counter.most_common(1)[0][0]]
            else:
                return []

        topics: List[Dict[str, Any]] = []
        for idx, phrase in enumerate(top_phrases):
            # Ищем сообщения, содержащие эту фразу (или её части для биграмм)
            phrase_tokens = phrase.split()
            matched_messages = []
            for msg, tokens in tokens_by_msg:
                # Для биграмм проверяем наличие обоих токенов рядом
                if len(phrase_tokens) == 2:
                    found = False
                    for i in range(len(tokens) - 1):
                        if tokens[i] == phrase_tokens[0] and tokens[i+1] == phrase_tokens[1]:
                            found = True
                            break
                    if found:
                        matched_messages.append(msg)
                else:
                    # Для униграмм просто проверяем наличие
                    if phrase in tokens:
                        matched_messages.append(msg)
            
            matched_messages = matched_messages[:10]
            msg_count = max(1, len(matched_messages))
            priority = "high" if idx == 0 else "medium"

            summary_lines: List[str] = []
            for candidate in matched_messages[:3]:
                summary_lines.append(candidate.get("content", "")[:160])
            summary = " ".join(line for line in summary_lines if line).strip()
            if not summary:
                summary = f"Обсуждение ключевой темы «{phrase}»."

            owners_counter: Counter[str] = Counter(
                mask_pii(msg.get("username") or "") for msg in matched_messages if msg.get("username")
            )
            owners_list = [
                owner for owner, _ in owners_counter.most_common(3) if owner
            ]
            owners_text = ", ".join(owners_list) if owners_list else "Активные участники группы"

            # Ищем решения в сообщениях темы
            decisions = self._extract_decisions_from_messages(matched_messages)
            decision_text = "Требуется зафиксировать итоговое решение."
            actions_list: List[str] = []
            if decisions:
                # Берём первое решение для decision
                first_decision = decisions[0]
                decision_text = first_decision.get("summary", decision_text)
                # Формируем actions из решений
                for dec in decisions[:3]:
                    owners_dec = dec.get("owners", [])
                    if owners_dec:
                        actions_list.append(f"{', '.join(owners_dec)} — {dec.get('summary', '')}")

            topic_highlights = [
                h
                for h in highlights
                if any(token in (h.get("description", "").lower() + " " + " ".join(h.get("labels", []))).lower() 
                       for token in phrase_tokens)
            ]
            signals = {
                "source": "heuristic",
                "keyword": phrase,
                "media_refs": len(topic_highlights),
            }

            # Собираем message_ids
            message_ids = [msg.get("message_id") for msg in matched_messages if msg.get("message_id")]

            topics.append(
                {
                    "title": phrase.capitalize() if len(phrase.split()) == 1 else phrase,
                    "priority": priority,
                    "msg_count": msg_count,
                    "threads": [],
                    "message_ids": message_ids,
                    "summary": summary,
                    "signals": signals,
                    "decision": decision_text,
                    "status": "ongoing" if not decisions else ("agreed" if any(d.get("status") == "agreed" for d in decisions) else "unclear"),
                    "owners": owners_list if owners_list else [],
                    "blockers": [],
                    "actions": actions_list if actions_list else [],
                }
            )

        return topics

    def _extract_decisions_from_messages(
        self, messages: Sequence[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Извлечение решений из сообщений через эвристики (независимо от тем)."""
        if not messages:
            return []

        # Паттерны маркеров решений
        decision_markers = [
            r"решили",
            r"договорились",
            r"договорились\s+что",
            r"окей",
            r"ок\s*[,\.]",
            r"по\s+рукам",
            r"давайте\s+так",
            r"давай\s+сделаем",
            r"будем\s+делать",
            r"надо\s+сделать",
            r"я\s+беру",
            r"я\s+сделаю",
            r"беру\s+на\s+себя",
            r"могу\s+сделать",
            r"предлагаю",
            r"закрепляем",
        ]
        
        rejection_markers = [
            r"нет\s*[,\.]",
            r"не\s+будем",
            r"это\s+плохая\s+идея",
            r"не\s+надо",
            r"отказываемся",
        ]
        
        confirmation_markers = [
            r"ок\s*[,\.]",
            r"давайте\s+так",
            r"по\s+рукам",
            r"согласен",
            r"поддерживаю",
        ]

        decisions: List[Dict[str, Any]] = []
        messages_list = list(messages)
        
        for i, msg in enumerate(messages_list):
            content = msg.get("content", "").lower()
            if not content:
                continue
            
            # Проверяем маркеры решений
            found_marker = None
            for marker in decision_markers:
                if re.search(marker, content, re.IGNORECASE):
                    found_marker = marker
                    break
            
            if not found_marker:
                continue
            
            # Определяем статус
            status = "unclear"
            # Проверяем на отклонение
            if any(re.search(rm, content, re.IGNORECASE) for rm in rejection_markers):
                status = "rejected"
            # Проверяем на подтверждение в следующих сообщениях
            elif any(re.search(cm, content, re.IGNORECASE) for cm in confirmation_markers):
                status = "agreed"
            else:
                # Проверяем следующие 1-2 сообщения на подтверждение
                for j in range(i + 1, min(i + 3, len(messages_list))):
                    next_content = messages_list[j].get("content", "").lower()
                    if any(re.search(cm, next_content, re.IGNORECASE) for cm in confirmation_markers):
                        status = "agreed"
                        break
                    if any(re.search(rm, next_content, re.IGNORECASE) for rm in rejection_markers):
                        status = "rejected"
                        break
                if status == "unclear":
                    # Если только предложение без подтверждений
                    if "предлагаю" in content or "давай" in content:
                        status = "proposed"
            
            # Извлекаем owners: автор + @упоминания
            owners: List[str] = []
            username = msg.get("username")
            if username and username not in ("user-unknown", "без никнейма"):
                owners.append(username)
            
            # Ищем @упоминания
            mentions = re.findall(r"@(\w+)", msg.get("content", ""), re.IGNORECASE)
            owners.extend([m for m in mentions if m not in owners])
            
            # Ищем прямые обращения типа "Аня, сделай"
            direct_mentions = re.findall(r"([А-ЯЁа-яё]+)\s*[,\.]\s*(?:сделай|сделай\s+пожалуйста|возьми|можешь)", content, re.IGNORECASE)
            for mention in direct_mentions:
                if mention and mention not in owners:
                    owners.append(mention)
            
            # Формируем summary из контекста
            context_start = max(0, i - 1)
            context_end = min(len(messages_list), i + 2)
            context_messages = messages_list[context_start:context_end]
            summary_parts = []
            for ctx_msg in context_messages:
                ctx_content = ctx_msg.get("content", "").strip()
                if ctx_content and len(ctx_content) < 200:
                    summary_parts.append(ctx_content)
            
            summary = " ".join(summary_parts[:2])[:300]
            if not summary:
                summary = msg.get("content", "")[:200]
            
            # Собираем message_ids
            message_ids = [m.get("message_id") for m in context_messages if m.get("message_id")]
            if not message_ids:
                message_ids = [msg.get("message_id")] if msg.get("message_id") else []
            
            decisions.append({
                "summary": summary.strip(),
                "owners": owners,
                "message_ids": message_ids,
                "status": status,
            })
        
        return decisions

    def _build_fallback_topic_from_messages(
        self, messages: Sequence[Dict[str, Any]], highlights: Sequence[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Создание fallback-темы из сообщений с использованием биграмм."""
        if not messages:
            return []
        
        highlights = highlights or []
        message_count = len(messages)
        
        # Собираем биграммы из всех сообщений
        bigram_counter: Counter[str] = Counter()
        tokens_by_msg: List[Tuple[Dict[str, Any], List[str]]] = []
        
        for msg in messages:
            media_summary = msg.get('media_summary', [])
            if not isinstance(media_summary, list):
                media_summary = []
            content = f"{msg.get('content', '')} {' '.join(media_summary)}"
            tokens = _tokenize(content)
            if not tokens:
                continue
            tokens_by_msg.append((msg, tokens))
            
            # Собираем биграммы
            for i in range(len(tokens) - 1):
                bigram = f"{tokens[i]} {tokens[i+1]}"
                bigram_counter[bigram] += 1
        
        if not bigram_counter:
            # Если биграмм нет, используем униграммы
            unigram_counter: Counter[str] = Counter()
            for msg, tokens in tokens_by_msg:
                unigram_counter.update(tokens)
            if not unigram_counter:
                # Если нет токенов вообще, создаём тему из первых слов сообщений
                title_parts = []
                for msg in messages[:5]:
                    content = (msg.get('content', '') or '').strip()
                    if content:
                        # Берём первые 2-3 слова
                        words = content.split()[:3]
                        if words:
                            title_parts.extend([w for w in words if len(w) > 2])
                            if len(title_parts) >= 2:
                                break
                if not title_parts:
                    # Последний fallback: просто "Обсуждение"
                    title = "Обсуждение"
                else:
                    title = f"Обсуждение: {' '.join(title_parts[:2])}"
            else:
                # Берём 2-3 самые частые униграммы
                top_tokens = [token for token, _ in unigram_counter.most_common(3)]
                title_parts = top_tokens[:3]
                # Формируем заголовок
                if len(title_parts) >= 2:
                    title = f"Обсуждение: {title_parts[0]} и {title_parts[1]}"
                else:
                    title = f"Обсуждение: {title_parts[0]}"
        else:
            # Берём 2-3 самые частые биграммы
            top_bigrams = [bigram for bigram, _ in bigram_counter.most_common(3)]
            title_parts = top_bigrams[:3]
            # Формируем заголовок
            if len(title_parts) >= 2:
                title = f"Обсуждение: {title_parts[0]} и {title_parts[1]}"
            else:
                title = f"Обсуждение: {title_parts[0]}"
        
        # Собираем все message_ids
        all_message_ids = [msg.get("message_id") for msg in messages if msg.get("message_id")]
        
        # Формируем summary из первых сообщений
        summary_parts = []
        for msg in messages[:5]:
            content = msg.get("content", "").strip()
            if content and len(content) < 200:
                summary_parts.append(content[:150])
        summary = " ".join(summary_parts[:3])[:400]
        if not summary:
            summary = f"Обсуждение из {message_count} сообщений."
        
        # Ищем решения
        decisions = self._extract_decisions_from_messages(messages)
        decision_text = "Явных решений не найдено в явной форме."
        if decisions:
            first_decision = decisions[0]
            decision_text = first_decision.get("summary", decision_text)
        
        # Собираем owners
        owners_counter: Counter[str] = Counter(
            mask_pii(msg.get("username") or "") for msg in messages if msg.get("username")
        )
        owners_list = [owner for owner, _ in owners_counter.most_common(3) if owner]
        
        # Формируем actions из решений
        actions_list: List[str] = []
        for dec in decisions[:3]:
            owners_dec = dec.get("owners", [])
            if owners_dec:
                actions_list.append(f"{', '.join(owners_dec)} — {dec.get('summary', '')}")
        
        return [{
            "title": title,
            "priority": "medium",
            "msg_count": message_count,
            "threads": [],
            "message_ids": all_message_ids,
            "summary": summary,
            "signals": {"source": "fallback"},
            "decision": decision_text,
            "status": "ongoing" if not decisions else ("agreed" if any(d.get("status") == "agreed" for d in decisions) else "unclear"),
            "owners": owners_list,
            "blockers": [],
            "actions": actions_list,
        }]

    def _normalize_topics(self, topics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Нормализация тем: убрать пустые, дубли, обрезать воду."""
        if not topics:
            return []
        
        normalized: List[Dict[str, Any]] = []
        seen_titles: set[str] = set()
        generic_titles = {"Общее обсуждение", "Разное", "Без темы", "Без названия"}
        
        for topic in topics:
            title = (topic.get("title") or "").strip()
            if not title or title in generic_titles:
                continue
            
            # Проверяем на дубли (нормализуем для сравнения)
            title_lower = title.lower()
            if title_lower in seen_titles:
                continue
            seen_titles.add(title_lower)
            
            # Обрезаем слишком длинные summary
            summary = topic.get("summary", "").strip()
            if len(summary) > 500:
                summary = summary[:497] + "..."
            
            # Убеждаемся, что есть обязательные поля
            # Нормализуем owners: может быть строкой или списком
            owners_raw = topic.get("owners", [])
            if isinstance(owners_raw, str):
                owners = [owners_raw] if owners_raw else []
            elif isinstance(owners_raw, list):
                owners = owners_raw
            else:
                owners = []
            
            # Нормализуем actions: должен быть списком
            actions_raw = topic.get("actions", [])
            if isinstance(actions_raw, str):
                actions = [actions_raw] if actions_raw else []
            elif isinstance(actions_raw, list):
                actions = actions_raw
            else:
                actions = []
            
            normalized_topic = {
                "title": title,
                "priority": topic.get("priority", "medium"),
                "msg_count": int(topic.get("msg_count", 0)),
                "threads": topic.get("threads", []),
                "message_ids": topic.get("message_ids", []),
                "summary": summary,
                "signals": topic.get("signals", {}),
                "decision": topic.get("decision", "Явных решений не найдено"),
                "status": topic.get("status", "unclear"),
                "owners": owners,
                "blockers": topic.get("blockers", []),
                "actions": actions,
            }
            
            normalized.append(normalized_topic)
        
        return normalized

    def _sanitize_messages(self, raw_messages: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        sanitized: List[Dict[str, Any]] = []
        for raw in sorted(raw_messages, key=lambda m: m.get("posted_at") or ""):
            posted_at = parse_timestamp(raw.get("posted_at"))
            timestamp_iso = format_timestamp(posted_at)
            media_entries, media_summary = self._normalize_media_entries(raw)
            base_content = mask_pii(raw.get("content") or "")
            enriched_content = base_content.strip()
            if media_summary:
                attachments = "; ".join(media_summary[:3])
                if enriched_content:
                    enriched_content = f"{enriched_content}\nВложения: {attachments}"
                else:
                    enriched_content = f"Вложения: {attachments}"
            reaction_count = int(raw.get("reaction_count") or 0)
            # Сохраняем tg_message_id для формирования ссылок
            tg_message_id = raw.get("tg_message_id") or raw.get("telegram_message_id")
            sanitized.append(
                {
                    "message_id": str(raw.get("id") or raw.get("tg_message_id") or uuid.uuid4().hex),
                    "tg_message_id": tg_message_id,  # Сохраняем для формирования ссылок
                    "timestamp_iso": timestamp_iso,
                    "timestamp_unix": posted_at.timestamp(),
                    "username": mask_pii(raw.get("sender_username") or f"user-{raw.get('sender_tg_id') or 'unknown'}"),
                    "telegram_id": raw.get("sender_tg_id"),
                    "content": enriched_content,
                    "raw_content": base_content,
                    "reply_to_id": extract_reply_to(raw),
                    "is_service": bool(raw.get("is_service")),
                    "reaction_count": reaction_count,
                    "media": media_entries,
                    "media_summary": media_summary,
                    "has_media": bool(media_entries),
                }
            )
        return sanitized

    def _normalize_historical_messages(
        self, historical_messages: Optional[Sequence[Dict[str, Any]]]
    ) -> List[Dict[str, Any]]:
        if not historical_messages:
            return []
        normalized: List[Dict[str, Any]] = []
        for raw in historical_messages:
            try:
                timestamp_iso = raw.get("timestamp_iso") or raw.get("timestamp")
                posted_at = parse_timestamp(timestamp_iso)
                normalized.append(
                    {
                        "message_id": str(raw.get("message_id") or raw.get("id") or uuid.uuid4().hex),
                        "timestamp_iso": format_timestamp(posted_at),
                        "timestamp_unix": posted_at.timestamp(),
                        "username": raw.get("username") or "history",
                        "telegram_id": raw.get("telegram_id"),
                        "content": raw.get("content") or "",
                    }
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("group_context_service.history_skip", reason=str(exc))
        return normalized

    def _deduplicate_messages(
        self,
        messages: Sequence[Dict[str, Any]],
        historical_messages: Sequence[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, List[str]], Dict[str, str], Dict[str, Dict[str, Any]]]:
        if not messages:
            return [], {}, {}, {}

        keep: List[Dict[str, Any]] = []
        duplicates: Dict[str, List[str]] = {}
        soft_links: Dict[str, str] = {}
        historical_links: Dict[str, Dict[str, Any]] = {}

        time_gap_seconds = max(0, int(self.config.dedup_time_gap_minutes * 60))

        for msg in messages:
            duplicate_of: Optional[Dict[str, Any]] = None
            soft_of: Optional[Dict[str, Any]] = None
            historical_of: Optional[Dict[str, Any]] = None
            historical_similarity: float = 0.0

            for candidate in reversed(keep[-64:]):
                if time_gap_seconds:
                    delta = abs(msg["timestamp_unix"] - candidate["timestamp_unix"])
                    if delta > time_gap_seconds:
                        continue
                similarity = text_similarity(msg["content"], candidate["content"])
                if similarity >= self.config.similarity_threshold:
                    duplicate_of = candidate
                    break
                if similarity >= self.config.soft_similarity_threshold:
                    soft_of = candidate
                    break

            if duplicate_of:
                duplicates.setdefault(duplicate_of["message_id"], []).append(msg["message_id"])
                continue

            if soft_of:
                soft_links[msg["message_id"]] = soft_of["message_id"]

            keep.append(msg)
            if historical_messages:
                for candidate in reversed(historical_messages[-256:]):
                    similarity = text_similarity(msg["content"], candidate.get("content", ""))
                    if similarity >= self.config.similarity_threshold:
                        historical_of = candidate
                        historical_similarity = similarity
                        break
                    if similarity >= self.config.soft_similarity_threshold and historical_of is None:
                        historical_of = candidate
                        historical_similarity = similarity

            if historical_of:
                historical_links[msg["message_id"]] = {
                    "matched_id": historical_of.get("message_id"),
                    "similarity": round(historical_similarity, 4),
                    "timestamp_iso": historical_of.get("timestamp_iso"),
                }

        return keep, duplicates, soft_links, historical_links

    def _select_top_messages(self, messages: Sequence[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        if len(messages) <= limit:
            return list(messages)
        ranking = self._rank_messages(messages, {}, {}, [])
        top_ids = {item["message_id"] for item in ranking[:limit]}
        selected = [msg for msg in messages if msg["message_id"] in top_ids]
        if len(selected) < limit:
            # Дополняем самыми свежими сообщениями
            remaining = [msg for msg in messages if msg["message_id"] not in top_ids]
            remaining_sorted = sorted(remaining, key=lambda item: item["timestamp_unix"], reverse=True)
            for extra in remaining_sorted[: limit - len(selected)]:
                selected.append(extra)
        return sorted(selected, key=lambda item: item["timestamp_unix"])

    def _rank_messages(
        self,
        messages: Sequence[Dict[str, Any]],
        soft_links: Dict[str, str],
        historical_links: Dict[str, Dict[str, Any]],
        historical_ranking: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not messages:
            return []

        latest_ts = max(msg["timestamp_unix"] for msg in messages)
        weights = self.config.scoring
        half_life_seconds = max(60, int(self.config.recency_half_life_minutes * 60))
        decay_lambda = math.log(2) / half_life_seconds

        ranking: List[Dict[str, Any]] = []
        for msg in messages:
            recency_delta = max(0.0, latest_ts - msg["timestamp_unix"])
            recency_component = math.exp(-decay_lambda * recency_delta)
            reply_component = 1.0 if msg.get("reply_to_id") else 0.0
            length_component = clamp(len(msg.get("content", "")) / 400.0)
            reaction_component = clamp(int(msg.get("reaction_count") or 0) / 5.0)
            media_component = clamp(len(msg.get("media_summary") or []) / 4.0)

            base_score = (
                weights.recency * recency_component
                + weights.reply * reply_component
                + weights.length * length_component
                + weights.reactions * reaction_component
                + weights.media * media_component
            )
            if msg["message_id"] in soft_links:
                base_score *= 0.85
            if msg["message_id"] in historical_links:
                base_score *= 0.7

            ranking.append(
                {
                    "message_id": msg["message_id"],
                    "score": round(base_score, 4),
                    "timestamp_iso": msg["timestamp_iso"],
                    "components": {
                        "recency": round(recency_component, 4),
                        "reply": reply_component,
                        "length": round(length_component, 4),
                        "reactions": round(reaction_component, 4),
                        "media": round(media_component, 4),
                        "soft_penalty": 0.15 if msg["message_id"] in soft_links else 0.0,
                        "historical_penalty": 0.3 if msg["message_id"] in historical_links else 0.0,
                    },
                }
            )

        if historical_ranking:
            ranking.extend(
                {
                    "message_id": f"hist::{entry.get('message_id')}",
                    "score": round(float(entry.get("score", 0)) * 0.5, 4),
                    "timestamp_iso": entry.get("timestamp_iso", ""),
                    "components": {
                        "recency": entry.get("components", {}).get("recency", 0),
                        "reply": entry.get("components", {}).get("reply", 0),
                        "length": entry.get("components", {}).get("length", 0),
                        "reactions": entry.get("components", {}).get("reactions", 0),
                        "soft_penalty": entry.get("components", {}).get("soft_penalty", 0),
                        "historical_penalty": 0.0,
                    },
                }
                for entry in historical_ranking[: self.config.top_ranked]
            )

        return sorted(ranking, key=lambda item: (item["score"], item["timestamp_iso"]), reverse=True)

    def _excerpt_messages(
        self,
        ranking: Sequence[Dict[str, Any]],
        messages: Sequence[Dict[str, Any]],
        limit: int,
    ) -> List[Dict[str, Any]]:
        if not ranking:
            return list(messages)[-limit:]

        selected_ids = [item["message_id"] for item in ranking[:limit]]
        lookup = {msg["message_id"]: msg for msg in messages}
        selected = [lookup[msg_id] for msg_id in selected_ids if msg_id in lookup]
        return sorted(selected, key=lambda item: item["timestamp_unix"])


__all__ = [
    "ContextAssemblyResult",
    "ContextConfig",
    "ContextScoringWeights",
    "GroupContextService",
    "TOKEN_REGEX",
    "build_conversation_excerpt",
    "build_participant_stats",
    "clamp",
    "extract_reply_to",
    "format_timestamp",
    "mask_pii",
    "parse_timestamp",
    "text_similarity",
]

