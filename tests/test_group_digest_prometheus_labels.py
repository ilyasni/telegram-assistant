"""
Context7: Тесты санитизации Prometheus метрик для групповых дайджестов.

Проверяет, что все метрики используют санитизированные значения labels,
чтобы избежать ошибки "Incorrect label names".
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from prometheus_client import CollectorRegistry, REGISTRY

# Context7: Импорт функции санитизации и метрик
from worker.tasks.group_digest_agent import (
    _sanitize_prometheus_label,
    digest_quality_score,
    digest_topics_empty_total,
    digest_pre_quality_failed_total,
    digest_skipped_total,
    digest_pro_quota_exceeded_total,
    digest_circuit_open_total,
    digest_synthesis_fallback_total,
    digest_tokens_total,
    digest_generation_seconds,
    digest_stage_status_total,
    digest_stage_latency_seconds,
    digest_dlq_total,
    digest_messages_processed_total,
    digest_mode_total,
    GroupDigestOrchestrator,
    GroupDigestState,
)
from worker.tasks.digest_worker import (
    _sanitize_prometheus_label as worker_sanitize,
    digest_jobs_processed_total,
    digest_worker_generation_seconds,
    digest_worker_send_seconds,
    group_digest_quality_scores,
)


class TestSanitizePrometheusLabel:
    """Context7: Тесты универсальной функции санитизации."""

    def test_sanitize_valid_string(self):
        """Проверка санитизации валидной строки."""
        assert _sanitize_prometheus_label("test_value") == "test_value"
        assert _sanitize_prometheus_label("test123") == "test123"

    def test_sanitize_invalid_characters(self):
        """Проверка санитизации невалидных символов."""
        assert _sanitize_prometheus_label("test:value") == "test_value"
        assert _sanitize_prometheus_label("test-value") == "test_value"
        assert _sanitize_prometheus_label("test.value") == "test_value"
        assert _sanitize_prometheus_label("test/value") == "test_value"
        assert _sanitize_prometheus_label("test@value") == "test_value"

    def test_sanitize_multiple_underscores(self):
        """Проверка удаления множественных подчеркиваний."""
        assert _sanitize_prometheus_label("test___value") == "test_value"
        assert _sanitize_prometheus_label("test---value") == "test_value"

    def test_sanitize_leading_trailing_underscores(self):
        """Проверка удаления подчеркиваний в начале и конце."""
        assert _sanitize_prometheus_label("_test_value_") == "test_value"
        assert _sanitize_prometheus_label("__test__") == "test"

    def test_sanitize_numeric_prefix(self):
        """Проверка добавления префикса для значений, начинающихся с цифры."""
        assert _sanitize_prometheus_label("123test") == "label_123test"
        assert _sanitize_prometheus_label("0value") == "label_0value"

    def test_sanitize_none_and_empty(self):
        """Проверка обработки None и пустых значений."""
        assert _sanitize_prometheus_label(None) == "unknown"
        assert _sanitize_prometheus_label("") == "unknown"
        assert _sanitize_prometheus_label("   ") == "unknown"

    def test_sanitize_non_string(self):
        """Проверка обработки не-строковых значений."""
        assert _sanitize_prometheus_label(123) == "123"
        assert _sanitize_prometheus_label(True) == "True"
        assert _sanitize_prometheus_label(["test"]) == "['test']"

    def test_worker_sanitize_consistency(self):
        """Context7: Проверка консистентности функции санитизации в worker."""
        test_cases = [
            "test:value",
            "test-value",
            "test.value",
            None,
            "",
            "123test",
        ]
        for case in test_cases:
            assert worker_sanitize(case) == _sanitize_prometheus_label(case), \
                f"Несоответствие для {case}"


class TestPrometheusMetricsSanitization:
    """Context7: Тесты санитизации всех Prometheus метрик."""

    def test_digest_quality_score_labels(self):
        """Проверка санитизации labels для digest_quality_score."""
        # Context7: Тестируем с невалидными символами
        test_cases = [
            ("faithfulness", "faithfulness"),
            ("coherence", "coherence"),
            ("overall", "overall"),
            ("test:metric", "test_metric"),
            ("test-metric", "test_metric"),
        ]
        
        for input_value, expected in test_cases:
            sanitized = _sanitize_prometheus_label(input_value)
            assert sanitized == expected or sanitized.startswith("label_"), \
                f"Неверная санитизация для {input_value}: {sanitized}"
            
            # Context7: Проверяем, что метрика принимает санитизированное значение
            try:
                digest_quality_score.labels(metric=sanitized).set(0.5)
            except Exception as e:
                pytest.fail(f"Метрика не приняла санитизированное значение {sanitized}: {e}")

    def test_digest_skipped_total_labels(self):
        """Проверка санитизации labels для digest_skipped_total."""
        test_cases = [
            ("empty_window", "unknown", "normal"),
            ("too_few_messages", "tenant:123", "micro"),
            ("quality_below_threshold", "tenant-456", "large"),
            ("missing_scope", "tenant.789", "normal"),
        ]
        
        for reason, tenant_id, mode in test_cases:
            sanitized_reason = _sanitize_prometheus_label(reason)
            sanitized_tenant = _sanitize_prometheus_label(tenant_id)
            sanitized_mode = _sanitize_prometheus_label(mode)
            
            try:
                digest_skipped_total.labels(
                    reason=sanitized_reason,
                    tenant_id=sanitized_tenant,
                    mode=sanitized_mode,
                ).inc()
            except Exception as e:
                pytest.fail(f"Метрика не приняла санитизированные значения: {e}")

    def test_digest_jobs_processed_total_labels(self):
        """Проверка санитизации labels для digest_jobs_processed_total."""
        test_cases = [
            ("complete", "success"),
            ("generate", "failed"),
            ("send", "telegram_error"),
            ("complete", "non_retryable"),
            ("complete", "circuit_open"),
        ]
        
        for stage, status in test_cases:
            sanitized_stage = worker_sanitize(stage)
            sanitized_status = worker_sanitize(status)
            
            try:
                digest_jobs_processed_total.labels(
                    stage=sanitized_stage,
                    status=sanitized_status,
                ).inc()
            except Exception as e:
                pytest.fail(f"Метрика не приняла санитизированные значения: {e}")

    def test_digest_worker_generation_seconds_labels(self):
        """Проверка санитизации labels для digest_worker_generation_seconds."""
        for status in ["success", "failed"]:
            sanitized = worker_sanitize(status)
            try:
                digest_worker_generation_seconds.labels(status=sanitized).observe(1.0)
            except Exception as e:
                pytest.fail(f"Метрика не приняла санитизированное значение: {e}")

    def test_digest_worker_send_seconds_labels(self):
        """Проверка санитизации labels для digest_worker_send_seconds."""
        for status in ["success", "telegram_error", "failed"]:
            sanitized = worker_sanitize(status)
            try:
                digest_worker_send_seconds.labels(status=sanitized).observe(1.0)
            except Exception as e:
                pytest.fail(f"Метрика не приняла санитизированное значение: {e}")

    def test_all_metrics_with_real_data(self):
        """Context7: Комплексный тест всех метрик с реальными данными."""
        # Context7: Симулируем реальные сценарии использования метрик
        
        # 1. Quality scores
        for metric in ["faithfulness", "coherence", "coverage", "focus", "overall"]:
            sanitized = _sanitize_prometheus_label(metric)
            digest_quality_score.labels(metric=sanitized).set(0.75)
        
        # 2. Skipped digests
        digest_skipped_total.labels(
            reason=_sanitize_prometheus_label("empty_window"),
            tenant_id=_sanitize_prometheus_label("e70c43b0-e11d-45a8-8e51-f0ead91fb126"),
            mode=_sanitize_prometheus_label("normal"),
        ).inc()
        
        # 3. Topics empty
        digest_topics_empty_total.labels(
            reason=_sanitize_prometheus_label("no_topics_found"),
            tenant_id=_sanitize_prometheus_label("e70c43b0-e11d-45a8-8e51-f0ead91fb126"),
            mode=_sanitize_prometheus_label("normal"),
        ).inc()
        
        # 4. Pre-quality failed
        digest_pre_quality_failed_total.labels(
            check=_sanitize_prometheus_label("min_messages"),
            tenant_id=_sanitize_prometheus_label("e70c43b0-e11d-45a8-8e51-f0ead91fb126"),
        ).inc()
        
        # 5. Stage status
        digest_stage_status_total.labels(
            stage=_sanitize_prometheus_label("ingest_validator"),
            status=_sanitize_prometheus_label("success"),
        ).inc()
        
        # 6. DLQ
        digest_dlq_total.labels(
            stage=_sanitize_prometheus_label("evaluation_agent"),
            error_code=_sanitize_prometheus_label("invalid_json"),
        ).inc()
        
        # 7. Mode total
        digest_mode_total.labels(
            mode=_sanitize_prometheus_label("normal"),
            tenant_id=_sanitize_prometheus_label("e70c43b0-e11d-45a8-8e51-f0ead91fb126"),
            window_size_hours=_sanitize_prometheus_label("24"),
        ).inc()
        
        # 8. Worker metrics
        digest_jobs_processed_total.labels(
            stage=worker_sanitize("complete"),
            status=worker_sanitize("success"),
        ).inc()
        
        digest_worker_generation_seconds.labels(
            status=worker_sanitize("success"),
        ).observe(2.5)
        
        digest_worker_send_seconds.labels(
            status=worker_sanitize("success"),
        ).observe(0.5)
        
        # Context7: Проверяем, что все метрики записались без ошибок
        # Если дошли до этого места без исключений - тест пройден
        assert True


class TestPrometheusMetricsWithInvalidLabels:
    """Context7: Тесты обработки невалидных labels с реальными данными."""

    def test_invalid_labels_are_sanitized(self):
        """Проверка, что невалидные labels санитизируются автоматически."""
        invalid_labels = [
            "tenant:123",
            "tenant-456",
            "tenant.789",
            "tenant/abc",
            "tenant@def",
            "123tenant",
            "_tenant_",
            "tenant___id",
        ]
        
        for invalid_label in invalid_labels:
            sanitized = _sanitize_prometheus_label(invalid_label)
            
            # Context7: Проверяем, что санитизированное значение валидно
            assert sanitized.replace("_", "").replace("label_", "").isalnum() or sanitized == "unknown", \
                f"Санитизированное значение {sanitized} все еще содержит невалидные символы"
            
            # Context7: Проверяем, что метрика принимает санитизированное значение
            try:
                digest_skipped_total.labels(
                    reason=_sanitize_prometheus_label("test"),
                    tenant_id=sanitized,
                    mode=_sanitize_prometheus_label("normal"),
                ).inc()
            except Exception as e:
                pytest.fail(f"Метрика не приняла санитизированное значение {sanitized}: {e}")

    def test_real_tenant_ids_are_sanitized(self):
        """Context7: Тест с реальными UUID tenant_id."""
        real_tenant_ids = [
            "e70c43b0-e11d-45a8-8e51-f0ead91fb126",
            "50997e52-0fd7-460f-8791-3e59e6ac88c0",
            "cc1e70c9-9058-4fd0-9b52-94012623f0e0",
        ]
        
        for tenant_id in real_tenant_ids:
            sanitized = _sanitize_prometheus_label(tenant_id)
            
            # Context7: UUID содержит дефисы, которые должны быть заменены на подчеркивания
            assert "-" not in sanitized, f"Дефисы не удалены из {tenant_id}: {sanitized}"
            
            try:
                digest_skipped_total.labels(
                    reason=_sanitize_prometheus_label("test"),
                    tenant_id=sanitized,
                    mode=_sanitize_prometheus_label("normal"),
                ).inc()
            except Exception as e:
                pytest.fail(f"Метрика не приняла санитизированный tenant_id {sanitized}: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

