# Следующие шаги после исправления промпта

**Дата**: 2025-12-05  
**Context7**: План действий после применения улучшенного промпта для OCR entities

---

## Context

Промпт для извлечения OCR entities улучшен. Теперь нужно:
1. Протестировать изменения
2. Проверить результаты
3. Задокументировать процесс

---

## Следующие шаги

### 1. ✅ Проверка изменений в коде

**Выполнено**:
- ✅ Промпт улучшен для универсальности
- ✅ Добавлено детальное логирование
- ✅ Улучшена обработка ошибок
- ✅ Синтаксис проверен

**Файлы изменены**:
- `api/worker/services/ocr_enhancement_service.py` (промпт + логирование)

---

### 2. ⏭️ Применение изменений

#### Перезапуск worker

Для применения нового промпта нужно перезапустить worker:

```bash
# Перезапуск worker контейнера
docker restart telegram-assistant-worker-1

# Проверка логов после перезапуска
docker logs telegram-assistant-worker-1 2>&1 | tail -50
```

**Ожидаемый результат**:
- Worker успешно запускается
- OCR Enhancement Service инициализирован с новым промптом
- Логи показывают: `OCR Enhancement Service initialized`

---

### 3. ⏭️ Тестирование на реальных данных

#### Вариант A: Тест через скрипт (без перезапуска worker)

```bash
# Запуск теста на реальном тексте
python3 scripts/test_extract_entities_real.py
```

**Что проверяет**:
- Извлечение entities из реального OCR текста
- Работу нового промпта
- Результаты извлечения

#### Вариант B: Тест через диагностический скрипт

```bash
# Диагностика извлечения entities
python3 scripts/diagnose_ocr_entities.py
```

**Что проверяет**:
- Находит пост с OCR текстом
- Проверяет конфигурацию OCR Enhancement Service
- Пытается извлечь entities
- Выдает рекомендации

---

### 4. ⏭️ Проверка логов worker

После перезапуска worker проверить логи:

```bash
# Проверка логов OCR enhancement
docker logs telegram-assistant-worker-1 2>&1 | grep -i "ocr.*enhance\|extract_entities\|entity.*extraction" | tail -30

# Проверка ошибок
docker logs telegram-assistant-worker-1 2>&1 | grep -i "error.*entity\|failed.*entity" | tail -20

# Проверка успешного извлечения
docker logs telegram-assistant-worker-1 2>&1 | grep -i "entity extraction completed\|entities extracted" | tail -20
```

**Ожидаемые логи**:
- `LLM response received for entity extraction` - получен ответ от LLM
- `Entity extraction completed` - извлечение завершено с количеством entities
- Отсутствие ошибок `Failed to parse entity extraction JSON`

---

### 5. ⏭️ Ре-обработка существующих постов (опционально)

Если нужно протестировать на уже существующих постах:

#### Вариант A: Через скрипт ре-индексации

```bash
# Создать скрипт для ре-индексации постов с OCR
python3 scripts/reindex_posts_with_ocr.py --limit 10
```

**Что делает**:
- Находит посты с OCR текстом
- Ре-обрабатывает их через OCR Enhancement Service
- Записывает entities в Neo4j

#### Вариант B: Через события Redis

```bash
# Отправить событие для ре-обработки конкретного поста
# (требует реализации механизма ре-триггера)
```

---

### 6. ⏭️ Проверка записи в Neo4j

После обработки проверить записи entities:

```bash
# Проверить общее количество OCR entities
docker exec telegram-assistant-neo4j-1 cypher-shell -u neo4j -p neo4j123 \
  "MATCH (e:Entity {source: 'ocr'}) RETURN count(e) as total_ocr_entities;"

# Проверить entities для конкретного поста
docker exec telegram-assistant-neo4j-1 cypher-shell -u neo4j -p neo4j123 \
  "MATCH (p:Post {post_id: '696157be-f911-496b-9944-22ae82e79a83'})-[r:MENTIONS]->(e:Entity) \
   WHERE r.source = 'ocr' \
   RETURN e.name, e.type, r.confidence LIMIT 10;"
```

---

### 7. ⏭️ Мониторинг метрик

Проверить Prometheus метрики:

```bash
# Проверить метрики извлечения entities
curl -s http://localhost:9090/api/v1/query?query=ocr_entities_extracted_total | jq

# Проверить ошибки парсинга
curl -s http://localhost:9090/api/v1/query?query=ocr_enhancement_errors_total | jq
```

---

## Приоритетные шаги

### Сейчас (высокий приоритет)

1. ✅ **Проверка синтаксиса** - выполнено
2. ⏭️ **Перезапуск worker** - для применения изменений
3. ⏭️ **Проверка логов** - убедиться, что нет ошибок

### Далее (средний приоритет)

4. ⏭️ **Тестирование на реальных данных** - через скрипты
5. ⏭️ **Проверка записи в Neo4j** - проверить результаты
6. ⏭️ **Мониторинг метрик** - отслеживать улучшения

### Позже (низкий приоритет)

7. ⏭️ **Ре-обработка существующих постов** - если нужно
8. ⏭️ **Документация результатов** - зафиксировать улучшения

---

## Checks

### Проверка изменений

```bash
# Проверить новый промпт в коде
grep -A 25 "entity_extraction_prompt" api/worker/services/ocr_enhancement_service.py | head -30

# Проверить логирование
grep -A 10 "LLM response received for entity extraction" api/worker/services/ocr_enhancement_service.py
```

### Проверка работоспособности

```bash
# Проверить синтаксис Python
python3 -m py_compile api/worker/services/ocr_enhancement_service.py

# Проверить импорты (в контейнере worker)
docker exec telegram-assistant-worker-1 python3 -c \
  "from services.ocr_enhancement_service import OCREnhancementService; print('OK')"
```

---

## Impact / Rollback

### Impact

**Положительные изменения**:
- ✅ Универсальный промпт для разных типов текстов
- ✅ Детальное логирование для диагностики
- ✅ Улучшенная обработка ошибок

**Требуется**:
- Перезапуск worker для применения изменений
- Тестирование на реальных данных

### Rollback

**Если что-то пойдет не так**:
1. Вернуть старый промпт в `ocr_enhancement_service.py`
2. Перезапустить worker
3. Проверить логи

---

## Выводы

1. ✅ **Изменения применены**: Промпт улучшен, логирование добавлено
2. ⏭️ **Требуется перезапуск**: Worker нужно перезапустить для применения
3. ⏭️ **Тестирование**: После перезапуска протестировать на реальных данных
4. ⏭️ **Мониторинг**: Отслеживать результаты и метрики

**Следующее действие**: Перезапустить worker и проверить логи.

---

**План следующих шагов готов!**

