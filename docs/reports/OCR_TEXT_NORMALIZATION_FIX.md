# Исправление нормализации плохоформатированного OCR текста

**Дата:** 2025-11-03 17:35 MSK  
**Статус:** ✅ **ИСПРАВЛЕНО**

---

## Проблема

В данные попадает плохоформатированный OCR текст с множественными переносами строк и неправильным форматированием:

```json
{
  "ocr": {
    "text": "Сколько воды нужно для производства разных товаров?\nИзмеряем в промтах\n1 промт = 2 мл воды\nСмартфон\n6 400 000\nпромтов\nДжинсы\n5 400 000\nпромтов\nФутболка\n1 300 000\nпромтов\nЛист бумаги\n2550\nпромтов",
    ...
  }
}
```

Этот текст используется для эмбеддингов и индексации без нормализации, что приводит к:
- Ухудшению качества эмбеддингов
- Избыточному расходу токенов
- Неправильному форматированию в Qdrant/Neo4j

---

## Исправление

### Context7 Best Practices применены

1. **Улучшена функция `normalize_text()`**:
   - Удаление всех видов whitespace (пробелы, табы, переносы строк)
   - NFC normalization для унификации Unicode
   - Удаление zero-width символов

2. **Добавлена нормализация OCR текста перед использованием**:
   - Vision OCR текст нормализуется перед добавлением в композицию
   - Crawl OCR текст нормализуется перед добавлением
   - Основной текст поста также нормализуется для консистентности

3. **Улучшена дедупликация**:
   - Используется `normalize_text()` для корректного сравнения
   - Объединение через пробел вместо двойных переносов строк

---

## Изменения в коде

### 1. `worker/ai_providers/embedding_service.py`

**Улучшена функция `normalize_text()`:**

```python
def normalize_text(s: str) -> str:
    """
    [C7-ID: EMBEDDING-TEXT-NORM-001] Нормализация текста для эмбеддингов.
    
    Context7 best practice: нормализация OCR и плохоформатированного текста.
    - NFC normalization (унификация Unicode символов)
    - Удаление zero-width символов
    - Схлопывание множественных пробелов и переносов строк в одиночные пробелы
    - Удаление начальных/конечных пробелов
    
    Особенно важно для OCR текста, который часто содержит:
    - Множественные переносы строк (\n\n\n)
    - Неправильные пробелы и табуляции
    - Zero-width символы
    """
    if not s:
        return ""
    # NFC normalization для унификации Unicode символов
    s = unicodedata.normalize("NFC", s)
    # Удаление zero-width символов
    s = s.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
    # Context7: Схлопывание всех видов whitespace (пробелы, табы, переносы строк) в одиночные пробелы
    # Используем \s+ который включает: пробелы, табы, переносы строк, non-breaking spaces и др.
    s = re.sub(r"\s+", " ", s)
    # Удаление начальных/конечных пробелов
    return s.strip()
```

**Изменения:**
- ✅ Добавлена проверка на пустую строку
- ✅ Удаление дополнительных zero-width символов (`\u200c`, `\u200d`)
- ✅ Улучшена документация с примерами проблем OCR текста
- ✅ `\s+` обрабатывает все виды whitespace (включая переносы строк)

### 2. `worker/tasks/indexing_task.py`

**Добавлена нормализация всех текстовых частей:**

#### Основной текст поста:
```python
# БЫЛО:
post_text = post_data.get('text', '')[:2000]
text_parts = [post_text] if post_text.strip() else []

# СТАЛО:
post_text_raw = post_data.get('text', '')
if post_text_raw and post_text_raw.strip():
    from ai_providers.embedding_service import normalize_text
    post_text = normalize_text(post_text_raw)[:2000]
    text_parts = [post_text]
else:
    text_parts = []
```

#### Vision description:
```python
# БЫЛО:
vision_desc = vision_data.get('description', '')
if vision_desc and len(vision_desc.strip()) >= 5:
    text_parts.append(vision_desc[:500])

# СТАЛО:
vision_desc = vision_data.get('description', '')
if vision_desc and len(vision_desc.strip()) >= 5:
    from ai_providers.embedding_service import normalize_text
    vision_desc_normalized = normalize_text(vision_desc)
    text_parts.append(vision_desc_normalized[:500])
```

#### Vision OCR text (КРИТИЧНО):
```python
# БЫЛО:
if ocr_text and ocr_text.strip():
    text_parts.append(ocr_text[:300])

# СТАЛО:
if ocr_text and ocr_text.strip():
    # Context7: [C7-ID: ocr-text-normalization-001] Нормализация OCR текста перед использованием
    # OCR текст часто содержит множественные переносы строк и плохое форматирование
    # Нормализация удаляет избыточные пробелы и переносы строк для улучшения качества эмбеддингов
    from ai_providers.embedding_service import normalize_text
    ocr_text_normalized = normalize_text(ocr_text)
    text_parts.append(ocr_text_normalized[:300])
```

#### Crawl OCR text:
```python
# БЫЛО:
if ocr_text and ocr_text.strip():
    text_parts.append(ocr_text[:300])

# СТАЛО:
if ocr_text and ocr_text.strip():
    from ai_providers.embedding_service import normalize_text
    ocr_text_normalized = normalize_text(ocr_text)
    text_parts.append(ocr_text_normalized[:300])
```

#### Дедупликация и объединение:
```python
# БЫЛО:
seen = set()
unique_parts = []
for part in text_parts:
    part_normalized = part.strip().lower()
    if part_normalized and part_normalized not in seen:
        seen.add(part_normalized)
        unique_parts.append(part.strip())
final_text = '\n\n'.join(unique_parts)

# СТАЛО:
from ai_providers.embedding_service import normalize_text
seen = set()
unique_parts = []
for part in text_parts:
    # Дополнительная нормализация для дедупликации
    part_normalized = normalize_text(part).lower()
    if part_normalized and part_normalized not in seen:
        seen.add(part_normalized)
        unique_parts.append(part)  # Уже нормализована выше
# Объединяем через пробел (не двойной перенос строки) для компактности
final_text = ' '.join(unique_parts) if unique_parts else ''
```

---

## Результаты

### До исправления:
```
OCR текст: "Сколько воды нужно для производства разных товаров?\nИзмеряем в промтах\n1 промт = 2 мл воды\nСмартфон\n6 400 000\nпромтов..."
```

### После исправления:
```
OCR текст (нормализован): "Сколько воды нужно для производства разных товаров? Измеряем в промтах 1 промт = 2 мл воды Смартфон 6 400 000 промтов..."
```

**Преимущества:**
- ✅ Улучшение качества эмбеддингов (меньше шума)
- ✅ Сокращение расхода токенов
- ✅ Консистентное форматирование в Qdrant/Neo4j
- ✅ Лучшая дедупликация текста

---

## Проверка

### Команды для проверки:

1. **Проверка нормализации в логах:**
```bash
docker logs telegram-assistant-worker-1 --since 10m | grep -E "(normalize|OCR|ocr_text)" | tail -20
```

2. **Проверка эмбеддингов:**
```bash
docker logs telegram-assistant-worker-1 --since 10m | grep -E "(Generated embedding|text_length)" | tail -10
```

3. **Проверка Qdrant payload:**
```bash
docker exec telegram-assistant-qdrant-1 curl -s "http://localhost:6333/collections/t{tenant_id}_posts/points/scroll" -H "Content-Type: application/json" -d '{"limit": 1}' | python3 -m json.tool | grep -A 20 "payload"
```

---

## Выводы

✅ **Проблема исправлена**:
- OCR текст теперь нормализуется перед использованием
- Все текстовые части (пост, vision, crawl) нормализуются консистентно
- Улучшена функция `normalize_text()` для обработки всех видов whitespace

---

## Влияние

**Затронутые компоненты:**
- ✅ `worker/tasks/indexing_task.py` - нормализация перед композицией текста
- ✅ `worker/ai_providers/embedding_service.py` - улучшена функция нормализации
- ✅ Эмбеддинги - теперь используют нормализованный текст
- ✅ Qdrant - payload содержит нормализованный текст
- ✅ Neo4j - узлы содержат нормализованный текст

**Обратная совместимость:**
- ✅ Сохранена - изменения только улучшают обработку
- ✅ Старые данные не требуют миграции (нормализация происходит на лету)

