# Проверка записи OCR в Qdrant

**Дата**: 2025-12-05  
**Context7**: Проверка как OCR текст записывается в Qdrant

---

## Context

Проверка того, как OCR текст используется при записи постов в Qdrant векторную базу данных.

---

## Анализ кода

### 1. Генерация эмбеддинга (`_generate_embedding`)

**Файл**: `api/worker/tasks/indexing_task.py` (строки 799-815)

**Логика**:
- OCR текст берется из `vision_data.get('ocr')`
- Приоритет: `text_enhanced` > `text` (fallback на оригинал если enhanced отсутствует)
- OCR текст нормализуется через `normalize_text()`
- Добавляется в `text_parts` с лимитом 300 символов
- Включается в финальный текст для генерации эмбеддинга

**Код**:
```python
# Vision OCR text
vision_ocr = vision_data.get('ocr')
if vision_ocr:
    if isinstance(vision_ocr, dict):
        # Приоритет: text_enhanced > text
        ocr_text = vision_ocr.get('text_enhanced') or vision_ocr.get('text', '')
    else:
        ocr_text = str(vision_ocr)
    
    if ocr_text and ocr_text.strip():
        ocr_text_normalized = normalize_text(ocr_text)
        text_parts.append(ocr_text_normalized[:300])  # Лимит 300 символов
```

**Статус**: ✅ OCR текст используется для генерации эмбеддинга

---

### 2. Payload в Qdrant (`_index_to_qdrant`)

**Файл**: `api/worker/tasks/indexing_task.py` (строки 1002-1035)

**Логика**:
- Vision данные добавляются в payload
- OCR текст **НЕ сохраняется** в payload
- В payload сохраняются только метаданные:
  - `is_meme`
  - `labels`
  - `objects`
  - `scene`
  - `nsfw_score`
  - `aesthetic_score`
  - `classification`
  - `dominant_colors`

**Код**:
```python
# Vision enrichment данные
vision_data = post_data.get('vision_data')
if vision_data and isinstance(vision_data, dict):
    vision_payload = {}
    
    # Обязательные поля для фильтрации
    if 'is_meme' in vision_data:
        vision_payload["is_meme"] = bool(vision_data['is_meme'])
    if 'labels' in vision_data:
        vision_payload["labels"] = labels[:20]
    # ... другие метаданные
    
    if vision_payload:
        payload["vision"] = vision_payload
```

**Статус**: ⚠️ OCR текст НЕ сохраняется в payload (только для эмбеддинга)

---

## Структура данных

### Эмбеддинг включает:
1. **Основной текст поста** (до 2000 символов)
2. **Vision description** (до 500 символов)
3. **Vision OCR text** (до 300 символов) ✅
4. **Crawl markdown** (до 1500 символов)
5. **Crawl OCR** (до 300 символов)

### Payload включает:
- Базовые поля: `post_id`, `tenant_id`, `channel_id`, `text_short`
- Vision метаданные: `is_meme`, `labels`, `objects`, `scene`, `nsfw_score`, `aesthetic_score`, `classification`
- Crawl метаданные: `has_crawl`, `html_key`, `word_count`
- Tags: массив тегов
- **OCR текст отсутствует** в payload

---

## Выводы

### ✅ Что работает:

1. **OCR текст используется для эмбеддинга**:
   - OCR текст включается в текст для генерации эмбеддинга
   - Используется `text_enhanced` если доступен
   - Нормализация текста перед добавлением
   - Лимит 300 символов для OCR

2. **Качество эмбеддингов**:
   - OCR текст влияет на векторное представление поста
   - Посты с OCR будут найдены при семантическом поиске по OCR тексту

### ⚠️ Ограничения:

1. **OCR текст не в payload**:
   - OCR текст не сохраняется в payload Qdrant
   - Нельзя фильтровать по OCR тексту напрямую
   - Нельзя получить OCR текст из Qdrant без запроса к БД

2. **Причины**:
   - Payload ограничен 64KB
   - OCR текст может быть длинным
   - Полные тексты хранятся в PostgreSQL/S3

---

## Рекомендации

### Context7 Best Practices:

1. ✅ **Использование OCR для эмбеддингов**: Корректно
   - OCR текст включен в векторное представление
   - Используется enhanced текст для лучшего качества

2. ✅ **Ограничение размера**: Корректно
   - Лимит 300 символов для OCR предотвращает переполнение

3. ✅ **Нормализация**: Корректно
   - Нормализация текста перед использованием

4. ⚠️ **Payload структура**: Следует ограничениям
   - OCR текст не в payload (это нормально для больших текстов)
   - Метаданные vision доступны для фильтрации

---

## Checks

### Проверка реальных записей:

```bash
# Проверка поста в Qdrant
docker exec telegram-assistant-worker-1 python3 \
  /opt/telegram-assistant/scripts/check_ocr_in_qdrant.py \
  <post_id>
```

**Ожидаемый результат**:
- Пост найден в Qdrant
- Payload содержит vision метаданные
- OCR текст НЕ в payload (но использован для эмбеддинга)

---

**Статус**: OCR текст корректно используется для эмбеддингов, но не сохраняется в payload (это нормально для больших текстов).

