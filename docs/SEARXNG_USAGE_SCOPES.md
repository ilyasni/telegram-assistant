# SearXNG - Области использования

**Дата**: 2025-11-06  
**Статус**: ✅ Документировано

## Обзор

SearXNG интегрирован в систему как **fallback механизм** для внешнего поиска (external search grounding). Используется только в определенных сценариях.

## Использование SearXNG по сценариям

### ✅ Вопросы (Questions) - ИСПОЛЬЗУЕТСЯ

**Сервис:** `RAGService` (`api/services/rag_service.py`)

**Логика использования:**
- SearXNG используется в двух режимах:
  1. **Fallback механизм** - если не найдено результатов в каналах
  2. **Обогащение ответов** - если результаты есть, но:
     - Низкая уверенность (confidence < threshold)
     - Мало результатов (< minimum_results_threshold)
     - Низкие scores результатов (средний score < score_threshold)
- Поиск выполняется в следующем порядке:
  1. **Qdrant** (векторный поиск) - вес 0.5
  2. **PostgreSQL FTS** (полнотекстовый поиск) - вес 0.2
  3. **Neo4j GraphRAG** (графовый поиск) - вес 0.3
  4. **SearXNG** (внешний поиск):
     - Fallback: если все выше не дали результатов
     - Обогащение: если результаты есть, но качество низкое

**Код:**

Fallback механизм (если результатов нет):
```python
# api/services/rag_service.py:994-1054
if not search_results:
    logger.warning("No search results found", query=query[:50])
    # Пробуем внешний поиск через SearXNG
    searxng_response = await self.searxng_service.search(
        query, str(user_id), lang="ru"
    )
    
    if searxng_response.results:
        # Формируем ответ из внешних источников
        external_sources = [...]
        result = RAGResult(
            answer=f"По вашему запросу найдена информация из внешних источников:\n\n" + ...,
            sources=external_sources,
            confidence=0.4,
            ...
        )
```

Обогащение ответов (если результаты есть, но качество низкое):
```python
# api/services/rag_service.py:1056-1088
# Проверка условий для обогащения
if await self._should_enrich_with_searxng(search_results, confidence, query):
    # Обогащаем внешними источниками
    enriched_sources, confidence_boost = await self._enrich_with_searxng(
        query=query,
        user_id=str(user_id),
        existing_sources=sources,
        lang="ru"
    )
    
    # Обновляем источники и confidence
    sources = enriched_sources
    confidence = min(1.0, confidence + confidence_boost)
```

**Когда используется:**

Fallback (нет результатов):
- Пользователь задает вопрос через `/ask` или голосовое сообщение
- В каналах пользователя нет релевантной информации
- SearXNG используется как единственный источник ответа

Обогащение (результаты есть, но качество низкое):
- Низкая уверенность (confidence < 0.5 по умолчанию)
- Мало результатов (< 3 по умолчанию)
- Низкие scores результатов (средний score < 0.6 по умолчанию)
- SearXNG дополняет существующие источники внешними

**Ограничения:**
- Rate limiting: 10 запросов в минуту на пользователя
- Кэширование: TTL 3600 секунд (1 час)
- Максимум результатов: 5 (fallback), 2 (обогащение)
- Категории: только `general`, `news`, `wikipedia`

**Конфигурация обогащения:**
```python
# api/config.py
searxng_enrichment_enabled: bool = True  # Включить/выключить обогащение
searxng_enrichment_confidence_threshold: float = 0.5  # Порог уверенности
searxng_enrichment_min_results_threshold: int = 3  # Минимум результатов
searxng_enrichment_score_threshold: float = 0.6  # Порог среднего score
searxng_enrichment_max_external_results: int = 2  # Максимум внешних результатов
```

### ❌ Дайджесты (Digests) - НЕ ИСПОЛЬЗУЕТСЯ

**Сервис:** `DigestService` (`api/services/digest_service.py`)

**Логика:**
- Работает **только с внутренними источниками**
- Собирает посты из каналов пользователя по тематикам из `digest_settings.topics`
- Использует:
  - PostgreSQL (посты из каналов)
  - Qdrant (векторный поиск для релевантности)
  - Neo4j (связанные темы через граф)
  - GigaChat LLM (генерация дайджеста)

**Почему не используется SearXNG:**
- Дайджесты формируются на основе **пользовательских каналов**
- Внешние источники не релевантны для персональных дайджестов
- Фокус на контенте, который пользователь уже подписан

### ❌ Тренды (Trends) - НЕ ИСПОЛЬЗУЕТСЯ

**Сервис:** `TrendDetectionService` (`api/services/trend_detection_service.py`)

**Логика:**
- Работает **только с внутренними источниками**
- Анализирует **ВСЕ посты** за период (не учитывая пользовательские настройки)
- Использует:
  - PostgreSQL (сбор всех постов за период)
  - Qdrant (векторные embedding для кластеризации)
  - Neo4j (community detection, анализ связей)
  - GigaChat LLM (multi-agent система для классификации трендов)

**Почему не используется SearXNG:**
- Тренды определяются на основе **внутренних данных** (посты в каналах)
- Внешние источники не нужны для анализа трендов в каналах
- Фокус на локальных трендах в подписанных каналах

## Архитектурное решение

### Fallback механизм

SearXNG используется как **graceful degradation**:
- Если внутренние источники не дали результатов → используем внешний поиск
- Это обеспечивает ответ даже при отсутствии релевантной информации в каналах

### Обогащение ответов

SearXNG используется для **обогащения ответов** при низком качестве результатов:
- Если результаты есть, но confidence низкий → дополняем внешними источниками
- Если результатов мало → дополняем внешними источниками
- Если scores низкие → дополняем внешними источниками
- Confidence корректируется на основе качества внешних источников (boost до 0.15)

### Приоритет источников

1. **Внутренние источники** (приоритет):
   - Qdrant (векторный поиск)
   - PostgreSQL FTS (ключевые слова)
   - Neo4j GraphRAG (графовые связи)

2. **Внешние источники**:
   - **Fallback**: SearXNG (если внутренние не дали результатов)
   - **Обогащение**: SearXNG (если результаты есть, но качество низкое)

### Безопасность

- ✅ Чёрный список доменов (torrent, adult, gambling, phishing)
- ✅ Sanitization URL (удаление tracking параметров)
- ✅ Валидация через Pydantic
- ✅ Rate limiting на пользователя
- ✅ Ограничение категорий

## Рекомендации

### Когда использовать SearXNG

✅ **Использовать:**
- Вопросы пользователей, когда нет результатов в каналах (fallback)
- Дополнение ответов внешними источниками при низком качестве (обогащение)
- Общие вопросы, не связанные с каналами
- Улучшение confidence при низкой уверенности

❌ **Не использовать:**
- Дайджесты (только внутренние источники)
- Тренды (только внутренние источники)
- Персональные рекомендации (только каналы пользователя)

### Настройка

Для отключения SearXNG:
```bash
SEARXNG_ENABLED=false
```

Для изменения лимитов:
```bash
SEARXNG_RATE_LIMIT_PER_USER=10  # запросов в минуту
SEARXNG_MAX_RESULTS=5            # максимум результатов
SEARXNG_CACHE_TTL=3600           # TTL кэша в секундах
```

Для настройки обогащения:
```bash
SEARXNG_ENRICHMENT_ENABLED=true  # включить/выключить обогащение
SEARXNG_ENRICHMENT_CONFIDENCE_THRESHOLD=0.5  # порог уверенности
SEARXNG_ENRICHMENT_MIN_RESULTS_THRESHOLD=3   # минимум результатов
SEARXNG_ENRICHMENT_SCORE_THRESHOLD=0.6       # порог среднего score
SEARXNG_ENRICHMENT_MAX_EXTERNAL_RESULTS=2    # максимум внешних результатов
```

## Ссылки

- [RAG Service](../api/services/rag_service.py)
- [Digest Service](../api/services/digest_service.py)
- [Trend Detection Service](../api/services/trend_detection_service.py)
- [SearXNG Service](../api/services/searxng_service.py)
- [SearXNG Setup](./SEARXNG_SETUP.md)

