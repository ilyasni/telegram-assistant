# Комплексный анализ качества OCR и улучшения

**Дата**: 2025-12-05  
**Context7**: Полный анализ качества OCR, проблем записи в Neo4j/Qdrant и предложения по улучшению

---

## Context

Комплексный анализ качества OCR и проблем записи в Neo4j и Qdrant с предложениями по улучшению согласно Context7 best practices.

---

## 1. Анализ текущего качества OCR

### Статистика (за последние 7 дней)

**Общие показатели**:
- Всего Vision записей: **1,131**
- С OCR текстом: **1,120** (99%)
- С enhanced текстом: **1,120** (100% покрытие)
- С entities: **0** (0% - проблема)
- С corrections: **1,100** (98%)

**Качественные показатели**:
- Средняя длина OCR текста: **231 символ**
- Среднее количество исправлений: **16.02** на пост
- Процент постов с исправлениями: **98%**

### ✅ Что работает хорошо

1. **Высокое покрытие OCR**:
   - 99% постов имеют OCR текст
   - 100% OCR текстов обрабатываются через enhancement

2. **Эффективная коррекция**:
   - 98% постов имеют исправления
   - В среднем 16 исправлений на пост
   - Используется гибридный подход (словарь + LLM)

3. **Enhanced текст**:
   - Все OCR тексты имеют enhanced версию
   - Используется для улучшения эмбеддингов

### ❌ Проблемы

1. **OCR Entities не извлекаются** (0%):
   - Проблема с промптом (исправлено, но не применено к старым данным)
   - LLM не возвращает entities или возвращает пустой массив

2. **Запись entities в Neo4j отсутствует**:
   - 0 Entity узлов с source='ocr'
   - 0 MENTIONS связей для OCR entities

---

## 2. Проблемы записи в Neo4j

### Текущее состояние

**Статус**:
- Посты: ✅ 6,954 записей
- Альбомы: ✅ 962 альбома
- OCR Entities: ❌ 0 записей

### Причины отсутствия OCR Entities

1. **Entities не извлекаются**:
   - Промпт был слишком узкий (исправлено)
   - LLM не находит entities в тексте

2. **Запись не выполняется**:
   - `create_ocr_entities()` вызывается только если `entities` не пустой
   - Проверка: `if ocr_entities and isinstance(ocr_entities, list) and len(ocr_entities) > 0`

3. **Улучшения применены, но требуют переобработки**:
   - Универсальный промпт применен
   - Детальное логирование добавлено
   - Требуется переобработка старых постов

---

## 3. Проблемы записи в Qdrant

### Текущее состояние

**Статус**: ✅ OCR текст используется для эмбеддингов

**Структура**:
- OCR текст включается в эмбеддинг (до 300 символов)
- OCR текст **НЕ** сохраняется в payload (нормально)

### Проблемы Payload

1. **OCR текст не в payload**:
   - ❌ Нельзя получить OCR текст из Qdrant напрямую
   - ✅ Это нормально (экономия места, полный текст в PostgreSQL)

2. **Vision метаданные в payload**:
   - ✅ is_meme, labels, objects, scene, nsfw_score, aesthetic_score
   - ❌ OCR preview отсутствует

3. **Рекомендация**: Добавить OCR preview (первые 100 символов) в payload для быстрого доступа

---

## 4. Предложения по улучшению

### 4.1 Улучшение качества OCR

#### A. Расширение domain словарей

**Проблема**: Текущие словари ориентированы на банки/финансы

**Решение**: Добавить словари для других доменов

```python
DOMAIN_DICTS = {
    "banks": [...],  # Существующие
    "politics": [
        "Европейская комиссия", "ЕС", "Еврактив", "правительство",
        "министерство", "парламент", "президент", "министр"
    ],
    "geography": [
        "Киев", "Москва", "Бельгия", "Россия", "Украина", "Европа"
    ],
    "media": [
        "Financial Times", "Reuters", "Bloomberg", "Ведомости"
    ]
}
```

**Context7 Best Practice**: Расширение словарей для лучшего покрытия

#### B. Улучшение промпта для entity extraction

**Статус**: ✅ Уже исправлено (универсальный промпт)

**Дополнительные улучшения**:
1. Добавить примеры из разных доменов
2. Добавить инструкции по обработке опечаток
3. Добавить контекстные примеры

#### C. Метрики качества

**Добавить метрики**:
- `ocr_quality_score`: Оценка качества OCR (на основе confidence, corrections)
- `ocr_entities_coverage`: Процент OCR текстов с извлеченными entities
- `ocr_enhancement_impact`: Влияние enhancement на качество эмбеддингов

### 4.2 Улучшение записи в Neo4j

#### A. Добавить OCR preview в Post node

**Текущее**: OCR данные не сохраняются в Post node

**Решение**: Добавить поле `ocr_preview` (первые 200 символов)

```cypher
MERGE (p:Post {post_id: $post_id})
SET p.ocr_preview = $ocr_preview  -- Первые 200 символов enhanced текста
SET p.has_ocr = true
```

**Context7 Best Practice**: Хранить только превью, полный текст в PostgreSQL

#### B. Улучшить запись OCR Entities

**Проблема**: Entities не записываются

**Решение**: 
1. Переобработать старые посты с новым промптом
2. Добавить retry логику для извлечения entities
3. Добавить метрики для мониторинга

#### C. Добавить индексы для OCR поиска

```cypher
CREATE INDEX ocr_preview_fulltext IF NOT EXISTS FOR (p:Post) ON (p.ocr_preview);
```

### 4.3 Улучшение записи в Qdrant

#### A. Добавить OCR preview в payload

**Текущее**: OCR текст не в payload

**Решение**: Добавить `ocr_preview` (первые 100 символов)

```python
payload = {
    # ... существующие поля ...
    "ocr_preview": ocr_text_enhanced[:100] if ocr_text_enhanced else None,
    "has_ocr": bool(ocr_text),
    "ocr_length": len(ocr_text) if ocr_text else 0,
}
```

**Преимущества**:
- Быстрый доступ к OCR без запроса к БД
- Фильтрация по наличию OCR
- Поиск по OCR preview

**Context7 Best Practice**: Превью для быстрого доступа, полный текст в PostgreSQL

#### B. Добавить OCR метаданные в payload

```python
payload = {
    # ... существующие поля ...
    "ocr": {
        "has_ocr": True,
        "entities_count": len(entities),
        "corrections_count": len(corrections),
        "enhanced": True,
        "preview": ocr_text_enhanced[:100]
    }
}
```

### 4.4 Метрики и мониторинг

#### Добавить Prometheus метрики

```python
ocr_quality_score = Histogram(
    'ocr_quality_score',
    'OCR quality score (0-1)',
    buckets=[0.0, 0.5, 0.7, 0.8, 0.9, 1.0]
)

ocr_entities_coverage = Counter(
    'ocr_entities_coverage_total',
    'OCR texts with extracted entities',
    ['status']  # with_entities, without_entities
)

ocr_enhancement_impact = Histogram(
    'ocr_enhancement_impact',
    'Impact of OCR enhancement on embedding quality'
)
```

---

## 5. План реализации

### Этап 1: Улучшение качества OCR (Приоритет: Высокий)

1. ✅ Исправить промпт entity extraction (выполнено)
2. ⏭️ Расширить domain словари
3. ⏭️ Добавить метрики качества
4. ⏭️ Переобработать старые посты

### Этап 2: Улучшение записи в Neo4j (Приоритет: Средний)

1. ⏭️ Добавить OCR preview в Post node
2. ⏭️ Создать скрипт для переобработки старых постов
3. ⏭️ Добавить индексы для OCR поиска

### Этап 3: Улучшение записи в Qdrant (Приоритет: Средний)

1. ⏭️ Добавить OCR preview в payload
2. ⏭️ Добавить OCR метаданные
3. ⏭️ Обновить валидацию payload

### Этап 4: Мониторинг и метрики (Приоритет: Низкий)

1. ⏭️ Добавить Prometheus метрики
2. ⏭️ Создать Grafana дашборды
3. ⏭️ Настроить алерты

---

## 6. Context7 Best Practices

### ✅ Применяемые практики

1. **Идемпотентность**: Все операции idempотентны
2. **Нормализация**: OCR текст нормализуется перед использованием
3. **Лимиты**: Ограничение размера (300 символов для эмбеддинга)
4. **Дедупликация**: Удаление дубликатов текста

### ⏭️ Рекомендуемые улучшения

1. **Превью данных**: OCR preview для быстрого доступа
2. **Метрики**: Детальные метрики качества
3. **Мониторинг**: Непрерывный мониторинг качества
4. **Retry логика**: Повторная обработка при ошибках

---

## Checks

### Проверка качества OCR

```bash
# Статистика OCR
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT 
    COUNT(*) as total,
    COUNT(CASE WHEN data->'ocr'->>'text' IS NOT NULL THEN 1 END) as with_ocr,
    COUNT(CASE WHEN jsonb_array_length(COALESCE(data->'ocr'->'entities', '[]'::jsonb)) > 0 THEN 1 END) as with_entities
FROM post_enrichment 
WHERE kind = 'vision' 
  AND created_at > NOW() - INTERVAL '7 days';
"
```

### Проверка Neo4j

```bash
# Количество OCR entities
docker exec telegram-assistant-neo4j-1 cypher-shell -u neo4j -p neo4j123 \
  "MATCH (e:Entity {source: 'ocr'}) RETURN count(e) as count;"
```

### Проверка Qdrant payload

```bash
# Проверка структуры payload
docker exec telegram-assistant-worker-1 python3 \
  /opt/telegram-assistant/scripts/check_ocr_in_qdrant.py <post_id>
```

---

## Выводы

### ✅ Текущее состояние

1. **OCR качество**: Хорошее (99% покрытие, 98% с исправлениями)
2. **OCR enhancement**: Отлично (100% покрытие)
3. **OCR entities**: Проблема (0% - требуется переобработка)

### ⏭️ Рекомендуемые улучшения

1. **Расширить domain словари** для лучшего покрытия
2. **Добавить OCR preview** в Neo4j и Qdrant payload
3. **Переобработать старые посты** с новым промптом
4. **Добавить метрики качества** для мониторинга

---

**Следующие шаги**: Реализовать улучшения согласно плану

