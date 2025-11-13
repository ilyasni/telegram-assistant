import datetime

from worker.services.group_context_service import (
    ContextConfig,
    ContextScoringWeights,
    GroupContextService,
)


def _iso(minutes: int) -> str:
    base = datetime.datetime(2025, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)
    return (base + datetime.timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def make_service() -> GroupContextService:
    config = ContextConfig(
        similarity_threshold=0.9,
        soft_similarity_threshold=0.8,
        dedup_time_gap_minutes=180,
        max_context_messages=10,
        top_ranked=5,
        recency_half_life_minutes=120,
        scoring=ContextScoringWeights(recency=0.5, reply=0.3, length=0.1, reactions=0.1, media=0.1),
    )
    return GroupContextService(config)


def test_group_context_service_dedup_and_ranking():
    service = make_service()
    window = {"window_id": "w1"}
    raw = [
        {
            "id": "1",
            "posted_at": _iso(0),
            "sender_tg_id": 1,
            "sender_username": "alice",
            "content": "Планируем релиз новой версии бота",
        },
        {
            "id": "2",
            "posted_at": _iso(3),
            "sender_tg_id": 1,
            "sender_username": "alice",
            "content": "Планируем релиз новой версии бота",  # Дубликат
        },
        {
            "id": "3",
            "posted_at": _iso(5),
            "sender_tg_id": 2,
            "sender_username": "bob",
            "content": "Нужно добавить тесты и обновить документацию",
            "reply_to": {"message_id": "1"},
            "reaction_count": 4,
        },
    ]
    history = [
        {
            "message_id": "hist-1",
            "timestamp_iso": _iso(-60),
            "username": "alice",
            "content": "Планируем релиз новой версии бота",
        }
    ]

    result = service.assemble(
        window=window,
        raw_messages=raw,
        tenant_id="tenant",
        trace_id="trace",
        max_messages=10,
        excerpt_limit=10,
        historical_messages=history,
    )

    assert len(result.sanitized_messages) == 2  # один дубликат удалён
    assert result.stats["duplicates_removed"] == 1
    kept_ids = {msg["message_id"] for msg in result.sanitized_messages}
    assert result.duplicates  # есть запись о дубликате
    duplicate_root = next(iter(result.duplicates.keys()))
    assert duplicate_root in kept_ids
    assert result.ranking
    top_entry = result.ranking[0]
    assert top_entry["components"]["recency"] >= result.ranking[-1]["components"]["recency"]
    assert result.conversation_excerpt
    assert result.stats["historical_matches"] == 1
    assert result.historical_links


def test_group_context_service_media_enrichment():
    service = make_service()
    window = {"window_id": "w2"}
    raw = [
        {
            "id": "1",
            "posted_at": _iso(0),
            "sender_tg_id": 1,
            "sender_username": "alice",
            "content": "",
            "media": [
                {"file_sha256": "sha1", "kind": "image", "description": "Фото прототипа"},
            ],
        },
        {
            "id": "2",
            "posted_at": _iso(10),
            "sender_tg_id": 2,
            "sender_username": "bob",
            "content": "",
            "media": [
                {"file_sha256": "sha2", "kind": "document"},
            ],
        },
    ]

    result = service.assemble(
        window=window,
        raw_messages=raw,
        tenant_id="tenant",
        trace_id="trace",
        max_messages=10,
        excerpt_limit=5,
    )

    assert result.media_stats["media_total"] == 2
    assert result.media_stats["media_messages"] == 2
    assert result.media_highlights
    first_message = result.sanitized_messages[0]
    assert first_message["has_media"] is True
    assert "Вложения:" in first_message["content"]
    alice_stats = next(stat for stat in result.participant_stats if stat["username"] == "alice")
    assert alice_stats["media_count"] == 1


def test_group_context_service_keyword_topics():
    service = make_service()
    messages = [
        {
            "message_id": "1",
            "timestamp_iso": _iso(0),
            "timestamp_unix": 0.0,
            "username": "alice",
            "telegram_id": 1,
            "content": "Готовим презентацию проекта граффити на городской выставке.",
            "media_summary": ["image: граффити макет"],
        },
        {
            "message_id": "2",
            "timestamp_iso": _iso(5),
            "timestamp_unix": 300.0,
            "username": "bob",
            "telegram_id": 2,
            "content": "Нужно согласовать бюджет и безопасность для выставки граффити.",
            "media_summary": [],
        },
    ]
    highlights = [
        {
            "description": "Image с макетом граффити",
            "labels": ["graffiti", "art"],
        }
    ]

    topics = service.build_keyword_topics(messages, highlights, limit=2)
    assert topics
    topic_titles = {topic["title"].lower() for topic in topics}
    assert "граффити" in topic_titles or "выставке" in topic_titles
    assert topics[0]["signals"]["source"] == "heuristic"

