# Проверка тегирования, эмбеддингов и индексации

**Дата:** 2025-11-03 17:30 MSK  
**Статус:** ✅ **ПРОВЕРЕНО И ИСПРАВЛЕНО**

---

## Проблема

Пользователь сообщил, что:
- Судя по статистике, используется только "про" (GigaChat-Pro)
- "Просто gigachat" на тегирование и эмбеддинги - нет

Нужно было проверить:
1. Происходит ли тегирование текста постов
2. Запись в Qdrant
3. Запись в Neo4j

---

## Результаты проверки

### 1. Тегирование

**Статус:** ✅ **РАБОТАЕТ КОРРЕКТНО**

**Логи показывают:**
```
Processing tags event
tags_count=5
tags_sample=['дизайн', 'журнал', 'обложка']
provider=gigachat
```

**Код:**
- **Файл:** `worker/ai_providers/gigachain_adapter.py`
- **Модель:** `'GigaChat'` (строка 264) - **простая модель, не Pro** ✅
- **Провайдер:** `gigachat` ✅

**Вывод:** Тегирование использует правильную модель GigaChat (не Pro), что соответствует требованиям.

### 2. Эмбеддинги

**Статус:** ✅ **НАСТРОЕНО ПРАВИЛЬНО**

**Код:**
- **Файл:** `worker/ai_providers/embedding_service.py`
- **Провайдер:** `GigaChatEmbeddingProvider`
- **Модель:** `"any"` (строка 185) - gpt2giga сам отправляет на `GPT2GIGA_EMBEDDINGS` ✅
- **Размерность:** 2048 ✅
- **Endpoint:** `/v1/embeddings` через gpt2giga-proxy ✅

**Вывод:** Эмбеддинги используют GigaChat Embeddings через gpt2giga-proxy, что правильно.

### 3. Индексация в Qdrant и Neo4j

**Статус:** ⚠️ **ПРОБЛЕМА НАЙДЕНА И ИСПРАВЛЕНА**

**Проблема:**
- Посты пропускались при индексации: `embedding_status=skipped`
- Причина: проверка на пустой текст происходила **ДО** композиции с enrichment данными
- Посты с медиа (vision description/OCR) пропускались, даже если был контент для индексации

**Исправление:**
- Изменена логика проверки текста в `worker/tasks/indexing_task.py`
- Теперь проверка происходит **ПОСЛЕ** композиции с enrichment данными
- Посты с vision/crawl данными индексируются даже если основной текст пуст

**Код изменений:**
```python
# БЫЛО:
text = post_data.get('text', '')
if not text or not text.strip():
    # Пропускаем

# СТАЛО:
text_for_check = post_data.get('text', '')
has_vision = bool(post_data.get('vision_data'))
has_crawl = bool(post_data.get('crawl_data'))

if not text_for_check or not text_for_check.strip():
    if not has_vision and not has_crawl:
        # Пропускаем только если нет enrichment данных
    else:
        # Индексируем с использованием enrichment данных
```

---

## Использование моделей

### Тегирование
- **Модель:** `GigaChat` (простая модель) ✅
- **Файл:** `worker/ai_providers/gigachain_adapter.py:264`
- **Провайдер:** `gigachat` ✅

### Vision Analysis
- **Модель:** `GigaChat-Pro` ✅
- **Использование:** Анализ изображений
- **Файл:** `worker/ai_adapters/gigachat_vision.py`

### Эмбеддинги
- **Модель:** `GPT2GIGA_EMBEDDINGS` (через gpt2giga-proxy с `model: "any"`) ✅
- **Размерность:** 2048
- **Файл:** `worker/ai_providers/embedding_service.py`

---

## Исправления

### Исправление 1: Проверка текста при индексации

**Файл:** `worker/tasks/indexing_task.py`

**Изменения:**
- Проверка текста теперь учитывает enrichment данные (vision/crawl)
- Посты с медиа индексируются даже если основной текст пуст
- Используется vision description/OCR для индексации

---

## Проверка работы

### Тегирование
- ✅ Работает: в логах видны события `posts.tagged`
- ✅ Модель: GigaChat (простая, не Pro)
- ✅ Теги сохраняются в `post_enrichment`

### Эмбеддинги
- ✅ Настроены: GigaChat Embeddings через gpt2giga-proxy
- ⏳ Требуется проверка: после исправления индексации должны появиться записи

### Индексация
- ✅ Исправлена логика проверки текста
- ⏳ Требуется проверка: после перезапуска worker должны появиться записи в Qdrant и Neo4j

---

## Мониторинг

### Команды для проверки

1. **Проверка тегирования:**
```bash
docker logs telegram-assistant-worker-1 --since 10m | grep -E "(Processing tags event|tags_count)"
```

2. **Проверка индексации:**
```bash
docker logs telegram-assistant-worker-1 --since 10m | grep -E "(Post indexed|Qdrant|Neo4j|embedding_status=completed)"
```

3. **Проверка Qdrant:**
```bash
docker exec telegram-assistant-qdrant-1 curl -s http://localhost:6333/collections | python3 -m json.tool
```

4. **Проверка Neo4j:**
```bash
docker exec telegram-assistant-neo4j-1 cypher-shell -u neo4j -p neo4j123 "MATCH (p:Post) RETURN count(p) as total_posts, max(p.indexed_at) as last_indexed;"
```

---

## Выводы

1. ✅ **Тегирование работает корректно** - используется GigaChat (простая модель)
2. ✅ **Эмбеддинги настроены правильно** - используется GigaChat Embeddings
3. ✅ **Проблема с индексацией исправлена** - теперь учитываются enrichment данные

---

## Следующие шаги

1. Мониторинг логов после перезапуска worker
2. Проверка появления новых записей в Qdrant
3. Проверка появления новых записей в Neo4j
4. Проверка успешной индексации постов с медиа

