"""
Self-Improvement Loops - механизмы автоматического улучшения качества.

Performance guardrails:
- Self-verification: один короткий чеклист, максимум 200-300 токенов
- Self-correction: 1 попытка исправления, не цикл
- Self-ranking: максимум 2 кандидата (не 3-5)
- Self-gating: дает право на один retry с альтернативным промптом/моделью
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Any, Tuple
from uuid import UUID

import structlog

logger = structlog.get_logger(__name__)

# Импорт LLM инфраструктуры
try:
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_gigachat import GigaChat
    from langchain_core.output_parsers import StrOutputParser
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False
    logger.warning("LangChain GigaChat not available, self-improvement will use placeholders")

# Импорт credentials
try:
    from api.worker.tasks.group_digest_agent import load_gigachat_credentials
    CREDENTIALS_AVAILABLE = True
except ImportError:
    try:
        from worker.tasks.group_digest_agent import load_gigachat_credentials
        CREDENTIALS_AVAILABLE = True
    except ImportError:
        CREDENTIALS_AVAILABLE = False
        load_gigachat_credentials = None


class SelfVerificationResult:
    """Результат self-verification."""
    
    def __init__(
        self,
        passed: bool,
        issues: List[str],
        score: float,
    ):
        self.passed = passed
        self.issues = issues
        self.score = score


class SelfImprovementService:
    """Сервис для self-improvement механизмов."""
    
    def __init__(self, llm_router: Optional[Any] = None, llm_client: Optional[Any] = None):
        self._llm_router = llm_router
        self._llm_client = llm_client
        self._init_llm()
    
    def _init_llm(self):
        """Инициализация LLM клиента для self-improvement."""
        if not LLM_AVAILABLE or not CREDENTIALS_AVAILABLE:
            self._llm_chain = None
            return
        
        if self._llm_client:
            self._llm_chain = self._llm_client | StrOutputParser()
            return
        
        try:
            credentials = load_gigachat_credentials()
            # Формат credentials как в context_router_agent
            llm = GigaChat(
                credentials=credentials.get("credentials"),
                scope=credentials.get("scope", "GIGACHAT_API_PERS"),
                base_url=credentials.get("base_url"),
                verify_ssl_certs=credentials.get("verify_ssl_certs", False),
                model="GigaChat-Pro",  # Используем Pro для лучшего качества
                temperature=0.2,  # Низкая температура для более детерминированных результатов
                timeout=5.0,  # Короткий timeout для performance
            )
            self._llm_chain = llm | StrOutputParser()
        except Exception as e:
            logger.warning("self_improvement.llm_init_failed", error=str(e))
            self._llm_chain = None
    
    def self_verify(
        self,
        content: str,
        quality_checklist: List[str],
        max_tokens: int = 300,
    ) -> SelfVerificationResult:
        """
        Self-verification: LLM проверяет результат через чеклист.
        
        Performance: Один короткий чеклист, максимум 200-300 токенов.
        Только в Smart Path или для важных каналов.
        """
        if not self._llm_chain:
            # Fallback: если LLM недоступен, считаем что проверка прошла
            logger.debug("self_improvement.llm_unavailable", method="self_verify")
            return SelfVerificationResult(
                passed=True,
                issues=[],
                score=1.0,
            )
        
        try:
            # Формируем короткий промпт для проверки
            checklist_text = "\n".join(f"- {item}" for item in quality_checklist[:5])  # Максимум 5 пунктов
            
            prompt = ChatPromptTemplate.from_messages([
                ("system", "Ты проверяешь качество текста по чеклисту. Ответь строго JSON: {\"passed\": bool, \"issues\": [\"...\"], \"score\": 0.0-1.0}"),
                ("user", f"Текст для проверки:\n{content[:1000]}\n\nЧеклист:\n{checklist_text}\n\nПроверь текст и верни JSON.")
            ])
            
            chain = prompt | self._llm_chain
            result_text = chain.invoke({})
            
            # Парсинг JSON ответа
            result_text = result_text.strip().strip("```json").strip("```").strip()
            result_json = json.loads(result_text)
            
            return SelfVerificationResult(
                passed=result_json.get("passed", True),
                issues=result_json.get("issues", []),
                score=float(result_json.get("score", 1.0)),
            )
        except Exception as e:
            logger.warning("self_improvement.verify_failed", error=str(e))
            # При ошибке считаем что проверка прошла (fail-open)
            return SelfVerificationResult(
                passed=True,
                issues=[],
                score=1.0,
            )
    
    def self_correct(
        self,
        content: str,
        issues: List[str],
        max_attempts: int = 1,
    ) -> Optional[str]:
        """
        Self-correction: автоматическое исправление найденных проблем.
        
        Performance: 1 попытка исправления, не цикл.
        Только если качество сильно ниже порога (quality_score < 0.6).
        """
        if max_attempts > 1:
            logger.warning("self_improvement.max_attempts_exceeded", max_attempts=max_attempts)
            max_attempts = 1
        
        if not issues:
            return content
        
        if not self._llm_chain:
            # Fallback: если LLM недоступен, возвращаем исходный контент
            logger.debug("self_improvement.llm_unavailable", method="self_correct")
            return content
        
        try:
            # Формируем короткий промпт для исправления
            issues_text = "\n".join(f"- {issue}" for issue in issues[:3])  # Максимум 3 проблемы
            
            prompt = ChatPromptTemplate.from_messages([
                ("system", "Ты исправляешь текст, устраняя найденные проблемы. Верни только исправленный текст без дополнительных комментариев."),
                ("user", f"Исходный текст:\n{content[:2000]}\n\nНайденные проблемы:\n{issues_text}\n\nИсправь текст, устранив все проблемы.")
            ])
            
            chain = prompt | self._llm_chain
            corrected = chain.invoke({})
            
            return corrected.strip()
        except Exception as e:
            logger.warning("self_improvement.correct_failed", error=str(e))
            # При ошибке возвращаем исходный контент
            return content
    
    def self_rank(
        self,
        candidates: List[str],
        max_candidates: int = 2,
    ) -> Optional[str]:
        """
        Self-ranking: LLM выбирает лучший из кандидатов.
        
        Performance: Максимум 2 кандидата (не 3-5).
        Только в digest/trend пайплайнах, и только async.
        """
        if len(candidates) > max_candidates:
            logger.warning(
                "self_improvement.too_many_candidates",
                count=len(candidates),
                max=max_candidates
            )
            candidates = candidates[:max_candidates]
        
        if not candidates:
            return None
        
        if len(candidates) == 1:
            return candidates[0]
        
        if not self._llm_chain:
            # Fallback: если LLM недоступен, возвращаем первый кандидат
            logger.debug("self_improvement.llm_unavailable", method="self_rank")
            return candidates[0]
        
        try:
            # Формируем короткий промпт для выбора лучшего
            candidates_text = "\n\n".join(
                f"Кандидат {i+1}:\n{candidate[:1000]}" 
                for i, candidate in enumerate(candidates)
            )
            
            prompt = ChatPromptTemplate.from_messages([
                ("system", "Ты выбираешь лучший вариант из предложенных. Ответь строго JSON: {\"best\": 1 или 2, \"reason\": \"...\"}"),
                ("user", f"Варианты:\n{candidates_text}\n\nВыбери лучший вариант и верни JSON с номером (1 или 2) и кратким обоснованием.")
            ])
            
            chain = prompt | self._llm_chain
            result_text = chain.invoke({})
            
            # Парсинг JSON ответа
            result_text = result_text.strip().strip("```json").strip("```").strip()
            result_json = json.loads(result_text)
            
            best_index = int(result_json.get("best", 1)) - 1  # Индекс с 0
            if 0 <= best_index < len(candidates):
                return candidates[best_index]
            else:
                logger.warning("self_improvement.invalid_best_index", index=best_index, count=len(candidates))
                return candidates[0]
        except Exception as e:
            logger.warning("self_improvement.rank_failed", error=str(e))
            # При ошибке возвращаем первый кандидат
            return candidates[0]
    
    def self_gate(
        self,
        quality_score: float,
        threshold: float = 0.6,
    ) -> bool:
        """
        Self-gating: определяет, нужен ли retry с другой стратегией.
        
        Performance: Дает право на один retry с альтернативным промптом/моделью.
        При повторном фейле → задача уходит в DLQ.
        """
        if quality_score < threshold:
            logger.info(
                "self_improvement.gate_failed",
                quality_score=quality_score,
                threshold=threshold
            )
            return True  # Разрешить retry
        return False  # Не нужен retry

