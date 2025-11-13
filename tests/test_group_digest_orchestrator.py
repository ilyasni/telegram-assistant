import json

import pytest

pytest.importorskip("langgraph.graph")
pytest.importorskip("langchain_core.output_parsers")

from worker.tasks.group_digest_agent import (
    AgentSpec,
    GroupDigestConfig,
    GroupDigestOrchestrator,
    LLMResponse,
    ContextConfig,
    ContextScoringWeights,
    ContextStorageConfig,
    ResilienceConfig,
    RetrySettings,
    CircuitBreakerSettings,
)


class StubRouter:
    """Имитация LLMRouter для unit-тестов без реальных запросов к GigaChat."""

    def __init__(self) -> None:
        self.responses = {
            "segmenter_agent": LLMResponse(
                content=json.dumps(
                    {
                        "thread_id": "thread-1",
                        "units": [
                            {
                                "kind": "problem",
                                "text": "Сообщение о проблеме",
                                "msg_ids": ["1"],
                                "offset_range": [0, 42],
                                "confidence": 0.9,
                            }
                        ],
                    }
                ),
                model="GigaChat",
                prompt_alias="@base",
            ),
            "emotion_agent": LLMResponse(
                content=json.dumps(
                    {
                        "tone": "neutral",
                        "intensity": 0.4,
                        "conflict": 0.1,
                        "collaboration": 0.8,
                        "stress": 0.2,
                        "enthusiasm": 0.5,
                        "notes": "Спокойное обсуждение",
                    }
                ),
                model="GigaChat",
                prompt_alias="@base",
            ),
            "roles_agent": LLMResponse(
                content=json.dumps(
                    {
                        "participants": [
                            {
                                "username": "tester",
                                "roles": [{"name": "initiator", "weight": 0.8}],
                                "dominant_role": "initiator",
                                "message_ids": ["1"],
                                "comment": "Запустил обсуждение",
                            }
                        ]
                    }
                ),
                model="GigaChat",
                prompt_alias="@base",
            ),
            "topic_agent": LLMResponse(
                content=json.dumps(
                    {
                        "topics": [
                            {
                                "title": "Проблема с продуктом",
                                "priority": "high",
                                "msg_count": 1,
                                "threads": ["thread-1"],
                                "summary": "Обсуждали баг в продукте.",
                            }
                        ]
                    }
                ),
                model="GigaChat",
                prompt_alias="@base",
            ),
            "synthesis_agent": LLMResponse(
                content=(
                    "📊 <b>Дайджест: Test Group</b> | 24h | 2 сообщений\n"
                    "🎯 Основные темы: Проблема с продуктом\n"
                    "😐 Эмоциональный тон: Нейтральный\n"
                    "👥 Активные участники: tester — initiator\n"
                    "📝 Резюме: Выявлен баг, требуется исправление."
                ),
                model="GigaChat-Pro",
                prompt_alias="@pro",
            ),
            "evaluation_agent": LLMResponse(
                content=json.dumps(
                    {
                        "faithfulness": 0.9,
                        "coherence": 0.8,
                        "coverage": 0.75,
                        "focus": 0.7,
                        "quality_score": 0.78,
                        "notes": "Дайджест корректен.",
                    }
                ),
                model="GigaChat",
                prompt_alias="@base",
            ),
        }
        self.responses["segmenter_agent_repair"] = self.responses["segmenter_agent"]
        self.responses["emotion_agent_repair"] = self.responses["emotion_agent"]
        self.responses["roles_agent_repair"] = self.responses["roles_agent"]
        self.responses["topic_agent_repair"] = self.responses["topic_agent"]
        self.responses["evaluation_agent_repair"] = self.responses["evaluation_agent"]
        self.responses["synthesis_agent_retry"] = self.responses["synthesis_agent"]

    def is_ready(self) -> bool:
        return True

    def invoke(self, agent_name, prompt, variables, tenant_id, trace_id, estimated_tokens):
        result = self.responses.get(agent_name)
        if result is None:
            raise RuntimeError(f"No stub response configured for agent {agent_name}")
        return result


def make_test_config() -> GroupDigestConfig:
    agents = {
        "segmenter_agent": AgentSpec(model_alias="@base", temperature=0.1, max_tokens=1200),
        "segmenter_agent_repair": AgentSpec(model_alias="@base", temperature=0.0, max_tokens=900),
        "emotion_agent": AgentSpec(model_alias="@base", temperature=0.1, max_tokens=700),
        "emotion_agent_repair": AgentSpec(model_alias="@base", temperature=0.0, max_tokens=600),
        "roles_agent": AgentSpec(model_alias="@base", temperature=0.1, max_tokens=700),
        "roles_agent_repair": AgentSpec(model_alias="@base", temperature=0.0, max_tokens=700),
        "topic_agent": AgentSpec(model_alias="@base", temperature=0.1, max_tokens=1500),
        "topic_agent_repair": AgentSpec(model_alias="@base", temperature=0.0, max_tokens=1000),
        "synthesis_agent": AgentSpec(model_alias="@base", temperature=0.3, max_tokens=3500),
        "synthesis_agent_retry": AgentSpec(model_alias="@base", temperature=0.2, max_tokens=2800),
        "evaluation_agent": AgentSpec(model_alias="@base", temperature=0.0, max_tokens=800),
        "evaluation_agent_repair": AgentSpec(model_alias="@base", temperature=0.0, max_tokens=600),
    }
    resilience = ResilienceConfig(
        retry=RetrySettings(
            max_attempts=2,
            initial_interval=0.1,
            backoff_factor=2.0,
            max_interval=1.0,
            jitter=False,
        ),
        circuit_breaker=CircuitBreakerSettings(
            failure_threshold=3,
            recovery_timeout=5.0,
        ),
    )
    context = ContextConfig(
        similarity_threshold=0.8,
        soft_similarity_threshold=0.6,
        dedup_time_gap_minutes=120,
        max_context_messages=200,
        top_ranked=50,
        recency_half_life_minutes=120,
        scoring=ContextScoringWeights(recency=0.5, reply=0.3, length=0.1, reactions=0.1, media=0.1),
    )
    context_storage = ContextStorageConfig(
        enabled=False,
        base_url="",
        api_key=None,
        namespace_prefix="group-digest",
        timeout=5.0,
        history_windows=0,
        history_message_limit=0,
    )
    return GroupDigestConfig(
        base_model="GigaChat",
        pro_model="GigaChat-Pro",
        embeddings_model="EmbeddingsGigaR",
        fallback_enabled=True,
        fallback_metric="digest_synthesis_fallback_total",
        pro_quota_per_tenant=10,
        pro_token_budget=100000,
        quota_window_hours=24,
        min_messages=1,
        max_messages=100,
        chunk_size=25,
        thread_max_len=10,
        max_retries=2,
        resilience=resilience,
        context=context,
        context_storage=context_storage,
        agents=agents,
    )


def test_group_digest_orchestrator_stubbed():
    orchestrator = GroupDigestOrchestrator(config=make_test_config(), llm_router=StubRouter())

    payload = {
        "window": {"window_id": "window", "group_id": "group", "tenant_id": "tenant", "scopes": ["DIGEST_READ"]},
        "messages": [
            {
                "id": "1",
                "group_id": "group",
                "tenant_id": "tenant",
                "sender_username": "tester",
                "sender_tg_id": 123,
                "posted_at": "2025-11-09T09:00:00Z",
                "content": "Обнаружен баг в продукте",
            },
            {
                "id": "2",
                "group_id": "group",
                "tenant_id": "tenant",
                "sender_username": "tester",
                "sender_tg_id": 123,
                "posted_at": "2025-11-09T09:05:00Z",
                "content": "Нужно исправить в ближайшем релизе",
                "reply_to": {"message_id": "1"},
            },
        ],
    }

    result = orchestrator.generate(payload)

    assert result["summary_html"].startswith("📊 <b>Дайджест")
    assert result["topics"][0]["title"] == "Проблема с продуктом"
    assert result["participants"][0]["username"] == "tester"
    assert result["metrics"]["tone"] == "neutral"
    assert result["evaluation"]["faithfulness"] == 0.9
    assert result["evaluation"]["quality_score"] == pytest.approx(0.78)
    assert result["quality_pass"] is True
    assert result["baseline_delta"]["has_baseline"] is False
    assert result["baseline_delta"]["novel_topics"] == 1
    assert result["delivery"]["status"] == "pending"
    assert result["delivery"]["format"] == "telegram_html"
    assert result["context_stats"]["deduplicated_messages"] == 2
    assert result["context_stats"]["duplicates_removed"] == 0
    assert len(result["context_ranking"]) == 2
    assert result["context_history_links"] == {}


def test_group_digest_orchestrator_blocked_by_scope(monkeypatch):
    monkeypatch.setenv("DIGEST_DELIVERY_SCOPE", "DIGEST_READ")
    orchestrator = GroupDigestOrchestrator(config=make_test_config(), llm_router=StubRouter())

    payload = {
        "window": {"window_id": "window", "group_id": "group", "tenant_id": "tenant", "scopes": []},
        "messages": [
            {
                "id": "1",
                "group_id": "group",
                "tenant_id": "tenant",
                "sender_username": "tester",
                "sender_tg_id": 123,
                "posted_at": "2025-11-09T09:00:00Z",
                "content": "Обнаружен баг в продукте",
            },
            {
                "id": "2",
                "group_id": "group",
                "tenant_id": "tenant",
                "sender_username": "tester",
                "sender_tg_id": 123,
                "posted_at": "2025-11-09T09:05:00Z",
                "content": "Нужно исправить в ближайшем релизе",
                "reply_to": {"message_id": "1"},
            },
        ],
    }

    result = orchestrator.generate(payload)

    assert result["delivery"]["status"] == "blocked_rbac"
    assert result["delivery"]["reason"] == "missing_scope:DIGEST_READ"
    assert "quality_pass" in result and result["quality_pass"] is True
    assert "missing_scope" in "".join(result.get("errors", []))

