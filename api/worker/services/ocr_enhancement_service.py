"""
OCR Text Enhancement Service
[C7-ID: OCR-ENHANCEMENT-SERVICE-001]

Context7 best practice: гибридное исправление OCR текста с приоритетом на улучшение эмбеддингов.
Пайплайн: нормализация → spell correction → (enhanced текст) → embeddings + entity extraction.
"""

import hashlib
import json
import re
import time
import unicodedata
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone

import structlog
from langchain_gigachat import GigaChat
from langchain_core.prompts import ChatPromptTemplate
from prometheus_client import Counter, Histogram
from redis.asyncio import Redis

from config import settings
from ai_providers.embedding_service import normalize_text

logger = structlog.get_logger()

# ============================================================================
# PROMETHEUS METRICS
# ============================================================================

ocr_enhancement_total = Counter(
    'ocr_enhancement_total',
    'Total OCR texts processed',
    ['stage']  # normalize, spell, entities
)

ocr_spell_corrections_total = Counter(
    'ocr_spell_corrections_total',
    'Total spell corrections',
    ['method']  # dictionary, llm
)

ocr_spell_llm_fallback_total = Counter(
    'ocr_spell_llm_fallback_total',
    'Total LLM fallback calls for spell correction'
)

ocr_entities_extracted_total = Counter(
    'ocr_entities_extracted_total',
    'Total entities extracted from OCR',
    ['type']  # ORG, PRODUCT, PERSON, LOC
)

ocr_enhancement_duration_seconds = Histogram(
    'ocr_enhancement_duration_seconds',
    'OCR enhancement processing duration',
    ['stage']
)

ocr_enhancement_cache_hits_total = Counter(
    'ocr_enhancement_cache_hits_total',
    'OCR enhancement cache hits',
    ['type']  # spell, entities
)

# ============================================================================
# DOMAIN DICTIONARIES
# ============================================================================

DOMAIN_DICTS = {
    "banks": [
        "Газпромбанк", "Сбербанк", "ВТБ", "Альфа-Банк", "Тинькофф", "Райффайзенбанк",
        "Россельхозбанк", "Открытие", "Юмани", "МКБ", "Почта Банк", "Росбанк",
        "Уралсиб", "Совкомбанк", "Хоум Кредит", "Ренессанс Кредит"
    ],
    "acquiring": [
        "ЭКВАЙРИНГ", "эквайринг", "Эквайринг", "ЭКВАЙРИНГОВЫЙ", "эквайринговый",
        "эквайринговая система", "платежный терминал", "POS-терминал"
    ],
    "products": [
        "дебетовая карта", "кредитная карта", "дебетовая", "кредитная",
        "виртуальная карта", "предоплаченная карта", "корпоративная карта",
        "бизнес-карта", "премиум карта", "кэшбэк", "бонусы", "мили"
    ],
    "brands": [
        "Visa", "Mastercard", "МИР", "Мир", "UnionPay", "American Express",
        "Apple Pay", "Google Pay", "Samsung Pay", "Яндекс.Пэй", "СБП"
    ]
}

# Объединенный словарь для быстрого поиска
DOMAIN_DICT_FLAT = {}
for category, words in DOMAIN_DICTS.items():
    for word in words:
        DOMAIN_DICT_FLAT[word.lower()] = word
        DOMAIN_DICT_FLAT[word.upper()] = word
        DOMAIN_DICT_FLAT[word] = word

# ============================================================================
# OCR ENHANCEMENT SERVICE
# ============================================================================

class OCREnhancementService:
    """
    Сервис для улучшения качества OCR текста.
    
    Features:
    - Нормализация текста (Unicode, whitespace)
    - Гибридное исправление опечаток (словари + LLM fallback)
    - Извлечение сущностей для Neo4j (ORG, PRODUCT, PERSON, LOC)
    - Кэширование LLM запросов в Redis
    """
    
    def __init__(
        self,
        redis_client: Optional[Redis] = None,
        gigachat_adapter: Optional[Any] = None,
        enabled: bool = True,
        llm_fallback_enabled: bool = True,
        entity_extraction_enabled: bool = True
    ):
        """
        Инициализация OCR Enhancement Service.
        
        Args:
            redis_client: Redis клиент для кэширования
            gigachat_adapter: GigaChat адаптер (опционально, создаст свой если не передан)
            enabled: Включение/выключение пайплайна
            llm_fallback_enabled: Включение LLM fallback для spell correction
            entity_extraction_enabled: Включение извлечения сущностей
        """
        self.redis_client = redis_client
        self.enabled = enabled
        self.llm_fallback_enabled = llm_fallback_enabled
        self.entity_extraction_enabled = entity_extraction_enabled
        
        # Инициализация GigaChat для LLM запросов
        if gigachat_adapter:
            self.llm = gigachat_adapter
        else:
            # Context7: Используем gpt2giga-proxy как OpenAI-compatible endpoint
            api_base = getattr(settings, 'openai_api_base', None) or "http://gpt2giga-proxy:8090"
            api_base = api_base.rstrip("/")
            if not api_base.endswith("/v1"):
                api_base = f"{api_base}/v1"
            
            credentials = getattr(settings, 'gigachat_credentials', None)
            if credentials:
                if hasattr(credentials, 'get_secret_value'):
                    credentials = credentials.get_secret_value()
            
            scope = getattr(settings, 'gigachat_scope', None) or "GIGACHAT_API_PERS"
            if hasattr(scope, 'get_secret_value'):
                scope = scope.get_secret_value()
            
            try:
                self.llm = GigaChat(
                    credentials=credentials or "",
                    scope=scope,
                    model="GigaChat",
                    base_url=api_base,
                    temperature=0.1,  # Низкая температура для детерминированных результатов
                )
            except Exception as e:
                logger.warning("Failed to initialize GigaChat for OCR enhancement", error=str(e))
                self.llm = None
        
        # Промпт для spell correction
        self.spell_correction_prompt = ChatPromptTemplate.from_messages([
            ("system", """Ты — эксперт по исправлению опечаток в OCR тексте.

Исправь опечатки в тексте, сохраняя структуру и форматирование. Особое внимание на:
- Названия банков (Газпромбанк, Сбербанк, ВТБ и т.д.)
- Финансовые термины (эквайринг, дебетовая карта, кредитная карта)
- Названия продуктов и брендов (Visa, Mastercard, МИР)

Верни ТОЛЬКО исправленный текст без дополнительных комментариев."""),
            ("human", "Исправь опечатки в этом OCR тексте:\n\n{text}")
        ])
        
        # Промпт для entity extraction
        self.entity_extraction_prompt = ChatPromptTemplate.from_messages([
            ("system", """Ты — эксперт по извлечению сущностей из текста.

Извлеки сущности из OCR текста. Типы:
- ORG (обязательно): банки, магазины, сервисы
- PRODUCT (обязательно): тип карт, эквайринг, конкретные продукты
- PERSON (опционально): если явно присутствует (имена, должности)
- LOC (опционально): города, адреса

Верни ТОЛЬКО валидный JSON массив без дополнительного текста:
[{"text": "название", "type": "ORG|PRODUCT|PERSON|LOC", "confidence": 0.0-1.0}]"""),
            ("human", "Извлеки сущности из этого текста:\n\n{text}")
        ])
        
        # Инициализация spellchecker (lazy)
        self._spell_checker = None
        
        logger.info(
            "OCR Enhancement Service initialized",
            enabled=enabled,
            llm_fallback_enabled=llm_fallback_enabled,
            entity_extraction_enabled=entity_extraction_enabled
        )
    
    def _get_spell_checker(self):
        """Lazy инициализация spell checker."""
        if self._spell_checker is None:
            try:
                from spellchecker import SpellChecker
                # Context7: Поддержка русского и английского
                self._spell_checker = SpellChecker(language=['ru', 'en'])
            except ImportError:
                logger.warning("spellchecker not available, using domain dictionaries only")
                self._spell_checker = False
        return self._spell_checker
    
    def normalize_ocr_text(self, text: str) -> str:
        """
        Расширенная нормализация OCR текста.
        
        Context7: Улучшенная версия normalize_text() с дополнительной обработкой
        специальных символов и артефактов OCR.
        """
        if not text:
            return ""
        
        # Базовая нормализация (Unicode, whitespace)
        normalized = normalize_text(text)
        
        # Дополнительная очистка OCR артефактов
        # Удаление изолированных символов (вероятные ошибки OCR)
        normalized = re.sub(r'\s+[^\w\s]{1}\s+', ' ', normalized)
        
        # Нормализация кавычек
        normalized = normalized.replace('"', '"').replace('"', '"')
        normalized = normalized.replace(''', "'").replace(''', "'")
        
        return normalized.strip()
    
    async def _get_cache_key(self, text: str, operation: str, lang: str = "ru", profile: str = "default") -> str:
        """Генерация ключа кэша."""
        text_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]
        return f"ocr_{operation}:{text_hash}:{lang}:{profile}"
    
    async def _get_from_cache(self, cache_key: str) -> Optional[Any]:
        """Получение из кэша."""
        if not self.redis_client:
            return None
        
        try:
            cached = await self.redis_client.get(cache_key)
            if cached:
                ocr_enhancement_cache_hits_total.labels(type=cache_key.split(':')[1]).inc()
                return json.loads(cached)
        except Exception as e:
            logger.warning("Failed to get from cache", cache_key=cache_key, error=str(e))
        
        return None
    
    async def _set_to_cache(self, cache_key: str, value: Any, ttl: int = 604800):
        """Сохранение в кэш (TTL: 7 дней)."""
        if not self.redis_client:
            return
        
        try:
            await self.redis_client.setex(
                cache_key,
                ttl,
                json.dumps(value, ensure_ascii=False)
            )
        except Exception as e:
            logger.warning("Failed to set to cache", cache_key=cache_key, error=str(e))
    
    def _detect_language(self, text: str) -> Tuple[str, float]:
        """
        Определение языка текста.
        
        Returns:
            (language, confidence)
        """
        try:
            from langdetect import detect_langs
            langs = detect_langs(text)
            if langs:
                return langs[0].lang, langs[0].prob
        except Exception as e:
            logger.debug("Language detection failed", error=str(e))
        
        # Fallback: считаем русским если есть кириллица
        if re.search(r'[а-яёА-ЯЁ]', text):
            return "ru", 0.5
        return "en", 0.5
    
    async def correct_spelling_hybrid(
        self,
        text: str,
        lang: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Гибридное исправление опечаток: словари + LLM fallback.
        
        Args:
            text: Исходный OCR текст
            lang: Язык текста (опционально, определится автоматически)
        
        Returns:
            Dict с полями:
            - text_enhanced: исправленный текст
            - corrections: список исправлений
            - method: "dictionary" | "llm" | "hybrid"
        """
        start_time = time.time()
        
        if not text or not text.strip():
            return {
                "text_enhanced": text,
                "corrections": [],
                "method": "none"
            }
        
        # Нормализация перед обработкой
        normalized = self.normalize_ocr_text(text)
        
        # Определение языка
        if not lang:
            lang, lang_confidence = self._detect_language(normalized)
        else:
            lang_confidence = 0.9  # Предполагаем высокую уверенность если язык задан
        
        # Проверка кэша
        cache_key = await self._get_cache_key(normalized, "spell", lang, "default")
        cached = await self._get_from_cache(cache_key)
        if cached:
            ocr_enhancement_duration_seconds.labels(stage="spell").observe(time.time() - start_time)
            return cached
        
        corrections = []
        # Context7: Разбиваем на слова и разделители для правильного восстановления
        # Используем более точное разбиение для сохранения структуры
        word_pattern = re.compile(r'\b\w+\b')
        words = word_pattern.findall(normalized)
        word_positions = [(m.start(), m.end(), m.group()) for m in word_pattern.finditer(normalized)]
        unknown_tokens = []
        
        # Быстрый слой: проверка по доменным словарям и spellchecker
        spell_checker = self._get_spell_checker()
        
        corrected_words = {}
        for start, end, word in word_positions:
            word_lower = word.lower()
            
            # Проверка доменного словаря
            if word_lower in DOMAIN_DICT_FLAT:
                corrected = DOMAIN_DICT_FLAT[word_lower]
                if corrected != word:
                    corrections.append({
                        "original": word,
                        "corrected": corrected,
                        "confidence": 0.98,
                        "method": "dictionary"
                    })
                    corrected_words[(start, end)] = corrected
                    continue
            
            # Проверка spellchecker
            if spell_checker and spell_checker is not False:
                if word_lower not in spell_checker:
                    # Попытка исправления
                    try:
                        corrected_word = spell_checker.correction(word_lower)
                        if corrected_word and corrected_word != word_lower:
                            # Восстанавливаем регистр если нужно
                            if word[0].isupper():
                                corrected_word = corrected_word.capitalize()
                            corrections.append({
                                "original": word,
                                "corrected": corrected_word,
                                "confidence": 0.85,
                                "method": "dictionary"
                            })
                            corrected_words[(start, end)] = corrected_word
                            continue
                    except Exception as e:
                        logger.debug("Spellchecker correction failed", token=word, error=str(e))
            
            # Токен не исправлен
            unknown_tokens.append(word)
        
        # Восстанавливаем текст с исправлениями
        if corrected_words:
            # Сортируем позиции в обратном порядке для замены справа налево
            sorted_positions = sorted(corrected_words.keys(), reverse=True)
            text_parts = list(normalized)
            for start, end in sorted_positions:
                text_parts[start:end] = corrected_words[(start, end)]
            normalized = ''.join(text_parts)
        
        # Триггеры для LLM fallback
        needs_llm = False
        llm_reason = None
        
        if self.llm_fallback_enabled:
            # Триггер 1: низкая уверенность в языке
            if lang_confidence < 0.7:
                needs_llm = True
                llm_reason = "low_lang_confidence"
            
            # Триггер 2: много неизвестных токенов (>30%)
            elif len(unknown_tokens) > len(words) * 0.3:
                needs_llm = True
                llm_reason = "many_unknown_tokens"
            
            # Триггер 3: короткий критичный текст (<50 символов)
            elif len(normalized) < 50 and any(keyword in normalized.lower() for keyword in ["банк", "карт", "чек", "сумм"]):
                needs_llm = True
                llm_reason = "short_critical_text"
        
        # LLM fallback
        if needs_llm and self.llm:
            ocr_spell_llm_fallback_total.inc()
            
            try:
                # Работаем на фрагменте (первые 500 символов для экономии токенов)
                fragment = normalized[:500]
                
                prompt = self.spell_correction_prompt.format_messages(text=fragment)
                response = await self.llm.ainvoke(prompt)
                
                if hasattr(response, 'content'):
                    llm_corrected = response.content.strip()
                else:
                    llm_corrected = str(response).strip()
                
                # Сравнение и извлечение исправлений
                if llm_corrected != fragment:
                    # Простое сравнение для извлечения исправлений
                    llm_tokens = re.findall(r'\b\w+\b', llm_corrected)
                    fragment_tokens = re.findall(r'\b\w+\b', fragment)
                    
                    for orig, corr in zip(fragment_tokens, llm_tokens):
                        if orig.lower() != corr.lower() and orig != corr:
                            corrections.append({
                                "original": orig,
                                "corrected": corr,
                                "confidence": 0.90,
                                "method": "llm"
                            })
                    
                    # Заменяем фрагмент в исходном тексте
                    normalized = normalized.replace(fragment, llm_corrected, 1)
                
                ocr_spell_corrections_total.labels(method="llm").inc()
                
            except Exception as e:
                logger.error("LLM spell correction failed", error=str(e), reason=llm_reason)
        elif needs_llm and not self.llm:
            logger.warning("LLM fallback requested but LLM not available", reason=llm_reason)
        
        # Подсчет исправлений словарем
        dict_corrections = sum(1 for c in corrections if c["method"] == "dictionary")
        if dict_corrections > 0:
            ocr_spell_corrections_total.labels(method="dictionary").inc()
        
        # Формирование результата
        result = {
            "text_enhanced": normalized,
            "corrections": corrections,
            "method": "hybrid" if needs_llm else "dictionary",
            "llm_fallback_used": needs_llm,
            "llm_fallback_reason": llm_reason
        }
        
        # Кэширование
        await self._set_to_cache(cache_key, result)
        
        ocr_enhancement_duration_seconds.labels(stage="spell").observe(time.time() - start_time)
        
        return result
    
    async def extract_entities(self, text: str) -> List[Dict[str, Any]]:
        """
        Извлечение сущностей из OCR текста для Neo4j.
        
        Args:
            text: OCR текст (желательно уже исправленный)
        
        Returns:
            Список сущностей: [{"text": "...", "type": "ORG|PRODUCT|PERSON|LOC", "confidence": 0.0-1.0}]
        """
        start_time = time.time()
        
        if not text or not text.strip():
            return []
        
        if not self.entity_extraction_enabled:
            return []
        
        if not self.llm:
            logger.debug("Entity extraction requested but LLM not available")
            return []
        
        # Проверка кэша
        cache_key = await self._get_cache_key(text, "entities", "ru", "default")
        cached = await self._get_from_cache(cache_key)
        if cached:
            ocr_enhancement_duration_seconds.labels(stage="entities").observe(time.time() - start_time)
            return cached
        
        try:
            # Ограничиваем длину текста для экономии токенов
            text_for_extraction = text[:1000]
            
            prompt = self.entity_extraction_prompt.format_messages(text=text_for_extraction)
            response = await self.llm.ainvoke(prompt)
            
            if hasattr(response, 'content'):
                response_text = response.content.strip()
            else:
                response_text = str(response).strip()
            
            # Парсинг JSON ответа
            # Удаляем markdown code blocks если есть
            response_text = re.sub(r'```json\s*', '', response_text)
            response_text = re.sub(r'```\s*', '', response_text)
            response_text = response_text.strip()
            
            entities = json.loads(response_text)
            
            if not isinstance(entities, list):
                entities = []
            
            # Валидация и фильтрация
            valid_entities = []
            for entity in entities:
                if isinstance(entity, dict) and "text" in entity and "type" in entity:
                    entity_type = entity.get("type", "").upper()
                    if entity_type in ["ORG", "PRODUCT", "PERSON", "LOC"]:
                        valid_entities.append({
                            "text": entity.get("text", ""),
                            "type": entity_type,
                            "confidence": float(entity.get("confidence", 0.8))
                        })
                        ocr_entities_extracted_total.labels(type=entity_type).inc()
            
            # Кэширование
            await self._set_to_cache(cache_key, valid_entities)
            
            ocr_enhancement_duration_seconds.labels(stage="entities").observe(time.time() - start_time)
            
            return valid_entities
            
        except json.JSONDecodeError as e:
            response_preview = response_text[:200] if 'response_text' in locals() else "N/A"
            logger.error("Failed to parse entity extraction JSON", error=str(e), response=response_preview)
            return []
        except Exception as e:
            logger.error("Entity extraction failed", error=str(e))
            return []
    
    async def enhance_ocr_data(
        self,
        ocr_data: Dict[str, Any],
        post_id: Optional[str] = None,
        trace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Главный метод для улучшения OCR данных.
        
        Пайплайн: нормализация → spell correction → entity extraction
        
        Args:
            ocr_data: Исходные OCR данные с полем "text"
            post_id: ID поста (для логирования)
            trace_id: Trace ID (для логирования)
        
        Returns:
            Обновленные OCR данные с полями:
            - text: оригинальный текст (сохраняется)
            - text_enhanced: исправленный текст
            - corrections: массив исправлений
            - entities: извлеченные сущности
            - enhanced_at: timestamp обработки
        """
        if not self.enabled:
            return ocr_data
        
        start_time = time.time()
        
        original_text = ocr_data.get("text", "")
        if not original_text or not original_text.strip():
            return ocr_data
        
        # Этап 1: Нормализация
        ocr_enhancement_total.labels(stage="normalize").inc()
        normalize_start = time.time()
        normalized = self.normalize_ocr_text(original_text)
        ocr_enhancement_duration_seconds.labels(stage="normalize").observe(time.time() - normalize_start)
        
        # Этап 2: Spell correction
        ocr_enhancement_total.labels(stage="spell").inc()
        spell_result = await self.correct_spelling_hybrid(normalized)
        
        # Этап 3: Entity extraction (на исправленном тексте)
        entities = []
        if self.entity_extraction_enabled:
            ocr_enhancement_total.labels(stage="entities").inc()
            entities = await self.extract_entities(spell_result["text_enhanced"])
        
        # Формирование результата
        enhanced_ocr = ocr_data.copy()
        enhanced_ocr["text"] = original_text  # Сохраняем оригинал
        enhanced_ocr["text_enhanced"] = spell_result["text_enhanced"]
        enhanced_ocr["corrections"] = spell_result.get("corrections", [])
        enhanced_ocr["entities"] = entities
        enhanced_ocr["enhanced_at"] = datetime.now(timezone.utc).isoformat()
        enhanced_ocr["enhancement_version"] = "1.0"
        
        # Вычисление confidence (среднее по исправлениям или 0.8 по умолчанию)
        if spell_result.get("corrections"):
            avg_confidence = sum(c.get("confidence", 0.8) for c in spell_result["corrections"]) / len(spell_result["corrections"])
            enhanced_ocr["text_confidence"] = avg_confidence
        else:
            enhanced_ocr["text_confidence"] = 0.85  # Высокая уверенность если нет исправлений
        
        ocr_enhancement_duration_seconds.labels(stage="total").observe(time.time() - start_time)
        
        logger.debug(
            "OCR text enhanced",
            post_id=post_id,
            trace_id=trace_id,
            original_length=len(original_text),
            enhanced_length=len(spell_result["text_enhanced"]),
            corrections_count=len(spell_result.get("corrections", [])),
            entities_count=len(entities),
            method=spell_result.get("method", "none")
        )
        
        return enhanced_ocr

