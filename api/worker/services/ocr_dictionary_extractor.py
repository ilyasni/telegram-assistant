"""
OCR Dictionary Extractor для автоматического извлечения и обновления словарей.

Context7: Реализует автоматическое извлечение терминов из OCR текстов по аналогии с
trends_keyword_extractor.py. Термины извлекаются, категоризируются и обновляются
автоматически на основе статистики использования.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import structlog

logger = structlog.get_logger()


# ============================================================================
# OCR DICTIONARY EXTRACTOR
# ============================================================================


class OCRDictionaryExtractor:
    """
    Экстрактор терминов для OCR словарей.
    
    Автоматически извлекает часто встречающиеся термины из OCR текстов,
    категоризирует их и обновляет в БД по аналогии с trend_clusters.keywords.
    """

    def __init__(self, db_pool: Optional[asyncpg.Pool] = None):
        """
        Инициализация экстрактора терминов.
        
        Args:
            db_pool: Пул подключений к БД для сохранения терминов
        """
        self.db_pool = db_pool

    def extract_terms_from_ocr(
        self,
        ocr_text: str,
        min_length: int = 3,
        max_length: int = 50
    ) -> List[str]:
        """
        Извлечь термины из OCR текста.
        
        Args:
            ocr_text: OCR текст для обработки
            min_length: Минимальная длина термина
            max_length: Максимальная длина термина
        
        Returns:
            Список извлечённых терминов
        """
        if not ocr_text or not ocr_text.strip():
            return []
        
        # Нормализация текста
        normalized = re.sub(r'\s+', ' ', ocr_text.strip())
        
        # Извлечение слов (кириллица + латиница)
        word_pattern = re.compile(r'\b[а-яёА-ЯЁa-zA-Z]{' + str(min_length) + ',' + str(max_length) + r'}\b')
        words = word_pattern.findall(normalized)
        
        # Фильтрация стоп-слов
        stopwords = {
            'это', 'как', 'так', 'его', 'еще', 'уже', 'ли', 'или', 'для', 'при', 'без',
            'по', 'во', 'на', 'в', 'и', 'а', 'но', 'же', 'то', 'не', 'ни', 'да',
            'к', 'ко', 'из', 'под', 'над', 'от', 'до', 'если', 'чтобы',
            'the', 'a', 'an', 'and', 'or', 'of', 'in', 'on', 'to', 'is', 'are'
        }
        
        filtered_words = [
            w for w in words
            if w.lower() not in stopwords and len(w) >= min_length
        ]
        
        # Извлечение имен собственных (с большой буквы)
        proper_nouns = [
            w for w in filtered_words
            if w[0].isupper() and len(w) >= 4
        ]
        
        # Объединяем и дедуплицируем
        all_terms = list(set(filtered_words + proper_nouns))
        
        return all_terms[:100]  # Ограничиваем до 100 терминов

    async def update_dictionary(
        self,
        term: str,
        category: Optional[str] = None,
        confidence: float = 0.5,
        correction_example: Optional[Dict[str, str]] = None
    ) -> bool:
        """
        Обновить или создать запись в словаре.
        
        Args:
            term: Термин для обновления
            category: Категория термина (politics, geography, media, etc.)
            confidence: Уверенность в корректности термина (0-1)
            correction_example: Пример исправления (опционально)
        
        Returns:
            True если успешно обновлено
        """
        if not self.db_pool or not term or not term.strip():
            return False
        
        try:
            term_normalized = term.strip()
            now = datetime.now(timezone.utc)
            
            query = """
                INSERT INTO ocr_dictionaries (
                    term, category, frequency, confidence,
                    first_seen_at, last_seen_at, correction_examples, updated_at
                )
                VALUES ($1, $2, 1, $3, $4, $4, $5::jsonb, $4)
                ON CONFLICT (term, category)
                DO UPDATE SET
                    frequency = ocr_dictionaries.frequency + 1,
                    last_seen_at = $4,
                    updated_at = $4,
                    confidence = GREATEST(ocr_dictionaries.confidence, $3),
                    correction_examples = CASE
                        WHEN $5::jsonb != '[]'::jsonb 
                        THEN ocr_dictionaries.correction_examples || $5::jsonb
                        ELSE ocr_dictionaries.correction_examples
                    END
                RETURNING id;
            """
            
            # Подготовка correction_examples
            examples = []
            if correction_example:
                examples = [correction_example]
            
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    query,
                    term_normalized,
                    category,
                    confidence,
                    now,
                    examples
                )
            
            logger.debug(
                "OCR dictionary term updated",
                term=term_normalized,
                category=category,
                confidence=confidence
            )
            
            return True
            
        except Exception as e:
            logger.warning(
                "Failed to update OCR dictionary term",
                term=term,
                category=category,
                error=str(e)
            )
            return False

    async def get_dictionary_terms(
        self,
        category: Optional[str] = None,
        min_frequency: int = 1,
        limit: int = 1000
    ) -> Dict[str, str]:
        """
        Получить термины из словаря.
        
        Args:
            category: Фильтр по категории (опционально)
            min_frequency: Минимальная частота использования
            limit: Максимальное количество терминов
        
        Returns:
            Словарь {term: category} для быстрого поиска
        """
        if not self.db_pool:
            return {}
        
        try:
            query = """
                SELECT term, category
                FROM ocr_dictionaries
                WHERE frequency >= $1
            """
            params = [min_frequency]
            
            if category:
                query += " AND category = $2"
                params.append(category)
            
            query += " ORDER BY frequency DESC, last_seen_at DESC LIMIT $3"
            
            if category:
                params.append(limit)
            else:
                params.insert(1, limit)
                query = query.replace("$3", "$2").replace("$2", "$3")
            
            async with self.db_pool.acquire() as conn:
                rows = await conn.fetch(query, *params)
            
            result = {}
            for row in rows:
                term = row['term']
                cat = row['category'] or 'general'
                result[term.lower()] = term  # Нормализованный ключ -> оригинальный термин
                result[term.upper()] = term
                result[term] = term
            
            return result
            
        except Exception as e:
            logger.warning(
                "Failed to load OCR dictionary terms",
                category=category,
                error=str(e)
            )
            return {}

    async def batch_update_dictionary(
        self,
        terms: List[Tuple[str, Optional[str], float]]
    ) -> int:
        """
        Батч-обновление словаря.
        
        Args:
            terms: Список кортежей (term, category, confidence)
        
        Returns:
            Количество обновлённых терминов
        """
        if not self.db_pool or not terms:
            return 0
        
        updated = 0
        for term, category, confidence in terms:
            if await self.update_dictionary(term, category, confidence):
                updated += 1
        
        return updated

    async def categorize_term(
        self,
        term: str,
        context: Optional[str] = None
    ) -> Optional[str]:
        """
        Определить категорию термина на основе контекста.
        
        Args:
            term: Термин для категоризации
            context: Контекст (OCR текст или описание)
        
        Returns:
            Категория или None
        """
        if not term:
            return None
        
        term_lower = term.lower()
        
        # Простая эвристическая категоризация
        politics_keywords = ['президент', 'министр', 'правительство', 'комиссия', 'парламент']
        geography_keywords = ['город', 'страна', 'столица', 'регион']
        media_keywords = ['новость', 'статья', 'репортаж', 'интервью']
        
        if context:
            context_lower = context.lower()
            for keyword in politics_keywords:
                if keyword in context_lower:
                    return 'politics'
            for keyword in geography_keywords:
                if keyword in context_lower:
                    return 'geography'
            for keyword in media_keywords:
                if keyword in context_lower:
                    return 'media'
        
        # Если контекста нет, используем эвристики по самому термину
        if any(kw in term_lower for kw in politics_keywords):
            return 'politics'
        if any(kw in term_lower for kw in geography_keywords):
            return 'geography'
        if any(kw in term_lower for kw in media_keywords):
            return 'media'
        
        return None

