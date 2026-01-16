# Диагностика ошибки и настройка мониторинга - Завершено

**Дата**: 2025-12-05  
**Context7**: Полная диагностика ошибки '"text"' и настройка мониторинга новых постов

---

## Context

Выполнена диагностика ошибки `Entity extraction failed error='"text"'` и настроен мониторинг новых постов с OCR.

---

## Диагностика ошибки '"text"'

### Проблема

**Ошибка в логах**:
```
2025-12-05 09:26:21 [error] Entity extraction failed error='"text"'
```

**Анализ**:
- KeyError при доступе к словарю
- Небезопасный доступ к `spell_result["text_enhanced"]`
- Возможна ситуация, когда ключ отсутствует

### Решение

**Применено исправление**:

1. **Добавлена проверка типа**:
```python
if not isinstance(spell_result, dict):
    logger.warning("spell_result is not a dict, using normalized text", ...)
    spell_result = {"text_enhanced": normalized, "corrections": [], "method": "none"}
```

2. **Безопасный доступ с fallback**:
```python
text_enhanced = spell_result.get("text_enhanced") or normalized or original_text
```

3. **Использование безопасного значения**:
```python
if text_enhanced and text_enhanced.strip():
    entities = await self.extract_entities(text_enhanced)
```

**Файл**: `api/worker/services/ocr_enhancement_service.py` (строки 657-674)

**Статус**: ✅ Исправление применено

---

## Проверка вызова extract_entities()

### Текущий код

**Перед исправлением**:
```python
entities = await self.extract_entities(spell_result["text_enhanced"])  # Небезопасно!
```

**После исправления**:
```python
text_enhanced = spell_result.get("text_enhanced") or normalized or original_text
if text_enhanced and text_enhanced.strip():
    entities = await self.extract_entities(text_enhanced)  # Безопасно!
```

### Проверка типа данных

**Убедились, что передается строка**:
- ✅ `extract_entities()` ожидает `text: str`
- ✅ Передается `text_enhanced` (строка)
- ✅ Добавлена проверка `text_enhanced.strip()`

---

## Настройка мониторинга новых постов

### Скрипт мониторинга

**Файл**: `scripts/monitor_new_ocr_posts.sh`

**Функциональность**:
1. Поиск новых постов с OCR (за последний час)
2. Проверка записи entities в Neo4j
3. Статистика по типам entities
4. Проверка логов entity extraction
5. Проверка ошибок

**Использование**:
```bash
./scripts/monitor_new_ocr_posts.sh
```

---

## Checks

### Проверка исправлений

```bash
# Синтаксис Python
python3 -m py_compile api/worker/services/ocr_enhancement_service.py

# Проверка безопасного доступа
grep -A 5 "text_enhanced = spell_result.get" api/worker/services/ocr_enhancement_service.py
```

### Мониторинг новых постов

```bash
# Запуск скрипта мониторинга
./scripts/monitor_new_ocr_posts.sh

# Или вручную:
# Проверка новых постов
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c \
  "SELECT post_id, created_at, jsonb_array_length(COALESCE(data->'ocr'->'entities', '[]'::jsonb)) as entities_count \
   FROM post_enrichment \
   WHERE kind = 'vision' AND data->'ocr' IS NOT NULL \
     AND created_at > NOW() - INTERVAL '1 hour' \
   ORDER BY created_at DESC LIMIT 10;"

# Проверка entities в Neo4j
docker exec telegram-assistant-neo4j-1 cypher-shell -u neo4j -p neo4j123 \
  "MATCH (e:Entity {source: 'ocr'}) RETURN count(e) as total;"
```

---

## Impact / Rollback

### Impact

**Положительные изменения**:
- ✅ Предотвращает KeyError при отсутствии ключа
- ✅ Graceful degradation с fallback
- ✅ Автоматический мониторинг новых постов
- ✅ Детальное логирование для диагностики

### Rollback

**Если нужно откатить**:
1. Вернуть старый код доступа к словарю
2. Удалить проверку типа (не рекомендуется)

---

## Следующие шаги

### 1. Применить исправление

**Перезапустить worker**:
```bash
docker restart telegram-assistant-worker-1
```

### 2. Мониторинг

**Отслеживать новые посты**:
```bash
# Запускать скрипт периодически
./scripts/monitor_new_ocr_posts.sh

# Или мониторить в реальном времени
watch -n 300 ./scripts/monitor_new_ocr_posts.sh  # Каждые 5 минут
```

### 3. Проверка результатов

**После обработки новых постов**:
- Проверить логи entity extraction
- Проверить записи entities в Neo4j
- Убедиться, что ошибка '"text"' больше не возникает

---

## Выводы

1. ✅ **Ошибка диагностирована**: KeyError при доступе к словарю
2. ✅ **Исправление применено**: Безопасный доступ с fallback
3. ✅ **Мониторинг настроен**: Скрипт для автоматической проверки
4. ⏭️ **Требуется перезапуск**: Для применения исправления
5. ⏭️ **Ожидание новых постов**: Для проверки работы нового промпта

---

**Диагностика и мониторинг завершены!**

**Все готово для применения исправления и мониторинга новых постов!**

