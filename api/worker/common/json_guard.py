"""
Context7 JSON guardrail utilities for multi-agent pipeline outputs.

Основные возможности:
- Валидация JSON-ответов агентов по заданной схеме (Draft7).
- Форматирование ошибок для логов/промптов самовосстановления.
- Универсальный helper `try_self_repair` для вызова ремонтного промпта.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Sequence, Tuple

import structlog
from jsonschema import Draft7Validator, ValidationError
from langchain_core.prompts import ChatPromptTemplate

if TYPE_CHECKING:  # pragma: no cover
    from worker.tasks.group_digest_agent import LLMRouter, LLMResponse

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ValidationResult:
    """Результат проверки JSON по схеме."""

    valid: bool
    errors: Tuple[str, ...]

    @property
    def error_message(self) -> str:
        if not self.errors:
            return ""
        return "\n".join(self.errors)


def validate(schema: Dict[str, Any], payload: Any) -> ValidationResult:
    """Проверяет payload на соответствие JSON Schema."""
    validator = Draft7Validator(schema)
    errors: List[str] = []
    for error in validator.iter_errors(payload):
        errors.append(_format_error(error))
    return ValidationResult(valid=not errors, errors=tuple(errors))


def _format_error(error: ValidationError) -> str:
    path = "/".join(str(part) for part in error.absolute_path)
    if path:
        return f"{path}: {error.message}"
    return error.message


def try_self_repair(
    *,
    llm_router: LLMRouter,
    agent_name: str,
    repair_prompt: Optional[ChatPromptTemplate],
    schema: Dict[str, Any],
    variables: Dict[str, Any],
    tenant_id: str,
    trace_id: str,
    error_messages: Sequence[str],
    max_attempts: int = 1,
) -> Optional[Dict[str, Any]]:
    """
    Пытается восстановить JSON-ответ агента с помощью ремонтного промпта.

    Возвращает исправленный JSON (dict) или None, если восстановление не удалось.
    """
    if repair_prompt is None:
        return None

    for attempt in range(1, max_attempts + 1):
        try:
            response: "LLMResponse" = llm_router.invoke(
                agent_name,
                repair_prompt,
                variables,
                tenant_id,
                trace_id,
                _approx_tokens_from_payload(variables),
            )
            repaired = json.loads(response.content)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "json_guard.self_repair_failed",
                agent=agent_name,
                attempt=attempt,
                error=str(exc),
            )
            continue

        result = validate(schema, repaired)
        if result.valid:
            logger.info(
                "json_guard.self_repair_success",
                agent=agent_name,
                attempts=attempt,
            )
            return repaired

        variables["errors"] = format_errors(result.errors)

    return None


def build_repair_variables(
    *,
    context: Dict[str, Any],
    invalid_json: str,
    errors: Iterable[str],
) -> Dict[str, Any]:
    """
    Формирует переменные для ремонтного промпта.

    Отдельно добавляет поля invalid_json и errors (строкой).
    """
    repair_vars = dict(context)
    repair_vars["invalid_json"] = invalid_json
    repair_vars["errors"] = format_errors(errors)
    return repair_vars


def format_errors(errors: Iterable[str]) -> str:
    """Форматирует список ошибок в строку для промпта."""
    return "\n".join(str(err) for err in errors) or "unknown_error"


def _approx_tokens_from_payload(payload: Any) -> int:
    if payload in (None, "", {}):
        return 1
    try:
        dumped = json.dumps(payload, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        dumped = str(payload)
    # Эвристика: 4 символа ≈ 1 токен
    return max(1, int(len(dumped) * 0.25))


