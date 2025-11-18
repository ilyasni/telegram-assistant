import json
from typing import Any, Dict

import pytest
from jsonschema import validate

pytest.importorskip("langgraph.graph")
pytest.importorskip("langchain_core.output_parsers")

from worker.tasks.group_digest_agent import (
    AgentSpec,
    CircuitBreakerSettings,
    ContextConfig,
    ContextScoringWeights,
    ContextStorageConfig,
    GroupDigestConfig,
    GroupDigestOrchestrator,
    JSON_SCHEMAS,
    LLMResponse,
    ResilienceConfig,
    RetrySettings,
)


BASE_PAYLOAD: Dict[str, Any] = {
    "window": {
        "window_id": "window",
        "group_id": "group",
        "tenant_id": "tenant",
        "scopes": ["DIGEST_READ"],
        "window_start": "2025-11-09T09:00:00Z",
        "window_end": "2025-11-09T10:00:00Z",
        "message_count": 2,
        "participant_count": 1,
    },
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


def make_resilience() -> ResilienceConfig:
    return ResilienceConfig(
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
        resilience=make_resilience(),
        context=ContextConfig(
            similarity_threshold=0.8,
            soft_similarity_threshold=0.6,
            dedup_time_gap_minutes=120,
            max_context_messages=200,
            top_ranked=50,
            recency_half_life_minutes=120,
            scoring=ContextScoringWeights(recency=0.5, reply=0.3, length=0.1, reactions=0.1, media=0.1),
        ),
        context_storage=ContextStorageConfig(
            enabled=False,
            base_url="",
            api_key=None,
            namespace_prefix="group-digest",
            timeout=5.0,
            history_windows=0,
            history_message_limit=0,
        ),
        agents=agents,
    )


class StubRouter:
    """Базовый router с валидными ответами."""

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
                        ],
                        "role_profile": [
                            {
                                "username": "tester",
                                "roles": [{"name": "initiator", "weight": 0.8}],
                                "dominant_role": "initiator",
                                "message_ids": ["1"],
                                "comment": "Запустил обсуждение",
                            }
                        ],
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
                                "signals": {"stress": 0.2},
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
                        "coherence": 0.85,
                        "coverage": 0.78,
                        "focus": 0.8,
                        "quality_score": 0.83,
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


class LowQualityRouter(StubRouter):
    def __init__(self) -> None:
        super().__init__()
        self.responses["evaluation_agent"] = LLMResponse(
            content=json.dumps(
                {
                    "faithfulness": 0.5,
                    "coherence": 0.55,
                    "coverage": 0.52,
                    "focus": 0.48,
                    "quality_score": 0.51,
                    "notes": "Качество ниже порога.",
                }
            ),
            model="GigaChat",
            prompt_alias="@base",
        )
        self.responses["evaluation_agent_repair"] = self.responses["evaluation_agent"]


class FailingSynthesisRouter(StubRouter):
    def invoke(self, agent_name, prompt, variables, tenant_id, trace_id, estimated_tokens):
        if agent_name in {"synthesis_agent", "synthesis_agent_retry"}:
            raise RuntimeError("synthesis_error")
        return super().invoke(agent_name, prompt, variables, tenant_id, trace_id, estimated_tokens)


def run_pipeline(router) -> Dict[str, Any]:
    orchestrator = GroupDigestOrchestrator(config=make_test_config(), llm_router=router)
    return orchestrator.generate(BASE_PAYLOAD)


def test_contract_outputs_match_json_schema():
    result = run_pipeline(StubRouter())

    validate({"topics": result["topics"]}, JSON_SCHEMAS["topic_agent"])
    validate(
        {
            "participants": result["participants"],
            "role_profile": result["role_profile"],
        },
        JSON_SCHEMAS["roles_agent"],
    )
    validate(result["evaluation"], JSON_SCHEMAS["evaluation_agent"])

    assert result["quality_pass"] is True
    assert "quality_score" in result["evaluation"]
    assert result["baseline_delta"]["has_baseline"] in {True, False}


def test_low_quality_results_generate_dlq_event():
    result = run_pipeline(LowQualityRouter())

    assert result["quality_pass"] is False
    assert result["delivery"]["status"] == "blocked_quality"
    assert result["dlq_events"], "DLQ events must be recorded"
    assert any(event["error_code"] == "quality_below_threshold" for event in result["dlq_events"])


def test_synthesis_failure_routes_to_dlq_with_skip():
    result = run_pipeline(FailingSynthesisRouter())

    assert result["skip"] is True
    assert result["delivery"]["status"] == "blocked_failure"
    assert result["dlq_events"], "Synthesis failure should enqueue DLQ event"
    assert any(event["error_code"] == "synthesis_failed" for event in result["dlq_events"])

