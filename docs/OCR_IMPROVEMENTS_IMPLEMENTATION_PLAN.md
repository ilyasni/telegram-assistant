# План реализации улучшений OCR качества и записи

**Дата**: 2025-12-05  
**Context7**: Комплексный план реализации улучшений для OCR, Neo4j и Qdrant

---

## Context

Комплексный анализ выявил проблемы и возможности для улучшения. Этот документ описывает план реализации улучшений согласно Context7 best practices.

---

## 1. Анализ текущего состояния

### Статистика OCR (за 7 дней)

- ✅ Всего Vision записей: **1,131**
- ✅ С OCR текстом: **1,120** (99%)
- ✅ С enhanced текстом: **1,120** (100%)
- ❌ С entities: **0** (0% - проблема)
- ✅ С corrections: **1,100** (98%)

### Проблемы

1. **OCR Entities**: 0% покрытие (промпт исправлен, требует переобработки)
2. **Neo4j**: 0 Entity узлов с source='ocr'
3. **Qdrant**: OCR текст не в payload (нормально, но можно улучшить)

---

## 2. Реализованные улучшения

### ✅ 2.1 Добавление OCR preview в Qdrant payload

**Файл**: `api/worker/tasks/indexing_task.py`

**Изменения**:
- Добавлен OCR preview (первые 100 символов) в vision payload
- Добавлены OCR метаданные (entities_count, corrections_count, enhanced)

**Код**:
```python
vision_payload["ocr"] = {
    "has_ocr": True,
    "preview": ocr_text_enhanced[:100],
    "entities_count": len(ocr_entities),
    "corrections_count": len(ocr_corrections),
    "enhanced": bool(vision_ocr.get('text_enhanced'))
}
```

**Преимущества**:
- Быстрый доступ к OCR без запроса к БД
- Фильтрация по наличию OCR
- Мониторинг качества (entities_count, corrections_count)

---

## 3. План реализации улучшений

### Этап 1: Расширение domain словарей (Приоритет: Высокий)

**Файл**: `api/worker/services/ocr_enhancement_service.py`

**Добавить словари**:
- `politics`: Европейская комиссия, ЕС, правительство, министерство
- `geography`: Киев, Москва, Бельгия, Россия, Украина
- `media`: Financial Times, Reuters, Bloomberg, Ведомости
- `organizations`: ЕС, НАТО, ООН, МВФ

**Impact**: Улучшение качества spell correction для политических, географических и медиа текстов

---

### Этап 2: Добавление OCR preview в Neo4j (Приоритет: Средний)

**Файл**: `api/worker/integrations/neo4j_client.py`

**Изменения**:
1. Добавить параметр `ocr_preview` в `create_post_node()`
2. Обновить Cypher запрос для сохранения `ocr_preview`
3. Добавить поле `has_ocr` для фильтрации

**Cypher запрос**:
```cypher
MERGE (p:Post {post_id: $post_id})
SET p.ocr_preview = $ocr_preview,
    p.has_ocr = $has_ocr
```

**Преимущества**:
- Быстрый поиск постов с OCR в Neo4j
- Превью для отображения без запроса к БД

---

### Этап 3: Метрики качества OCR (Приоритет: Средний)

**Файл**: `api/worker/services/ocr_enhancement_service.py`

**Добавить метрики**:
- `ocr_quality_score`: Оценка качества (0-1)
- `ocr_entities_coverage`: Процент с entities
- `ocr_enhancement_impact`: Влияние на эмбеддинги

---

### Этап 4: Скрипт переобработки старых постов (Приоритет: Низкий)

**Создать скрипт**: `scripts/reprocess_ocr_entities.py`

**Функциональность**:
1. Найти посты с OCR без entities
2. Переобработать с новым промптом
3. Обновить entities в БД
4. Переиндексировать в Neo4j

---

## 4. Context7 Best Practices

### ✅ Применяемые практики

1. **Идемпотентность**: Все операции idempотентны
2. **Нормализация**: OCR текст нормализуется
3. **Лимиты**: Ограничение размера (100 символов для preview)
4. **Дедупликация**: Удаление дубликатов

### ⏭️ Рекомендуемые улучшения

1. **Превью данных**: OCR preview для быстрого доступа
2. **Метрики**: Детальные метрики качества
3. **Мониторинг**: Непрерывный мониторинг
4. **Retry логика**: Повторная обработка при ошибках

---

## 5. Checks

### Проверка реализованных улучшений

```bash
# Проверка OCR preview в Qdrant
docker exec telegram-assistant-worker-1 python3 \
  /opt/telegram-assistant/scripts/check_ocr_in_qdrant.py <post_id>

# Проверка расширенных словарей
grep -A 30 "DOMAIN_DICTS" api/worker/services/ocr_enhancement_service.py
```

---

## Выводы

### ✅ Реализовано

1. **OCR preview в Qdrant payload**: ✅ Добавлено
2. **Расширение словарей**: ⏭️ Требует реализации

### ⏭️ Требует реализации

1. **OCR preview в Neo4j**: Скрипт для добавления
2. **Метрики качества**: Prometheus метрики
3. **Переобработка старых постов**: Скрипт для переобработки

---

**Статус**: Частично реализовано, требует завершения согласно плану

