# Руководство по применению исправления OCR Entities

**Дата**: 2025-12-05  
**Context7**: Пошаговое руководство по применению и проверке исправления

---

## Context

Исправление промпта для извлечения OCR entities применено в коде. Теперь нужно проверить работоспособность и применить изменения.

---

## Шаг 1: Проверка изменений

### ✅ Проверка синтаксиса

```bash
cd /opt/telegram-assistant
python3 -m py_compile api/worker/services/ocr_enhancement_service.py
```

**Ожидаемый результат**: ✅ Синтаксис корректен

### ✅ Проверка импорта в worker

```bash
docker exec telegram-assistant-worker-1 python3 -c \
  "import sys; sys.path.insert(0, '/opt/telegram-assistant/api/worker'); \
   from services.ocr_enhancement_service import OCREnhancementService; \
   print('✅ Импорт успешен')"
```

**Ожидаемый результат**: ✅ Импорт успешен

---

## Шаг 2: Применение изменений

### Вариант A: Перезапуск worker (рекомендуется)

Для применения нового промпта:

```bash
# Перезапуск worker
docker restart telegram-assistant-worker-1

# Проверка логов после перезапуска
docker logs telegram-assistant-worker-1 2>&1 | tail -50
```

**Ожидаемый результат**:
- Worker успешно запускается
- Нет критических ошибок
- OCR Enhancement Service инициализирован

### Вариант B: Пересоздание контейнера (если нужно)

```bash
# Остановка и пересоздание
docker-compose restart worker

# Или полная пересборка (если код в Dockerfile)
docker-compose up -d --build worker
```

---

## Шаг 3: Проверка логов

### Проверка инициализации

```bash
# Проверка инициализации OCR Enhancement Service
docker logs telegram-assistant-worker-1 2>&1 | grep -i "OCR Enhancement Service initialized" | tail -5
```

**Ожидаемый результат**:
```
OCR Enhancement Service initialized enabled=True entity_extraction_enabled=True llm_fallback_enabled=True
```

### Проверка работы entity extraction

```bash
# Проверка логов извлечения entities
docker logs telegram-assistant-worker-1 2>&1 | grep -i "entity extraction\|extract_entities" | tail -20
```

**Ожидаемые логи** (после обработки OCR):
- `LLM response received for entity extraction` - получен ответ от LLM
- `Entity extraction completed` - извлечение завершено
- Количество найденных entities

### Проверка ошибок

```bash
# Проверка ошибок
docker logs telegram-assistant-worker-1 2>&1 | grep -i "error.*entity\|failed.*entity\|JSONDecodeError.*entity" | tail -10
```

**Ожидаемый результат**: Отсутствие ошибок (или логирование ошибок с деталями)

---

## Шаг 4: Тестирование

### Тест через скрипт диагностики

```bash
# Запуск диагностики
python3 scripts/diagnose_ocr_entities.py
```

**Что проверяет**:
- Находит пост с OCR текстом
- Проверяет конфигурацию
- Пытается извлечь entities
- Выдает рекомендации

### Тест на реальном тексте

```bash
# Тест на реальном OCR тексте
python3 scripts/test_extract_entities_real.py
```

**Что проверяет**:
- Извлечение entities из реального текста
- Работу нового промпта
- Результаты извлечения

---

## Шаг 5: Проверка записи в Neo4j

### Проверка количества OCR entities

```bash
# Общее количество OCR entities
docker exec telegram-assistant-neo4j-1 cypher-shell -u neo4j -p neo4j123 \
  "MATCH (e:Entity {source: 'ocr'}) RETURN count(e) as total_ocr_entities;"
```

**Ожидаемый результат**: Количество > 0 (после обработки новых постов)

### Проверка entities для конкретного поста

```bash
# Entities для тестового поста
docker exec telegram-assistant-neo4j-1 cypher-shell -u neo4j -p neo4j123 \
  "MATCH (p:Post {post_id: '696157be-f911-496b-9944-22ae82e79a83'})-[r:MENTIONS]->(e:Entity) \
   WHERE r.source = 'ocr' \
   RETURN e.name as name, e.type as type, r.confidence as confidence LIMIT 10;"
```

**Ожидаемые entities** (для тестового поста):
- "Европейская комиссия" (ORG)
- "Киев" (LOC)
- "де Бевер" (PERSON)
- "Бельгия" (LOC)

---

## Шаг 6: Мониторинг

### Проверка метрик Prometheus

```bash
# Метрики извлечения entities
curl -s "http://localhost:9090/api/v1/query?query=ocr_entities_extracted_total" | jq '.data.result[]'

# Метрики ошибок
curl -s "http://localhost:9090/api/v1/query?query=ocr_enhancement_errors_total" | jq '.data.result[]'
```

### Проверка метрик в Grafana

- Дашборд: OCR Enhancement
- Метрики:
  - `ocr_entities_extracted_total` - количество извлеченных entities
  - `ocr_enhancement_duration_seconds` - длительность извлечения
  - `ocr_enhancement_errors_total` - количество ошибок

---

## Troubleshooting

### Проблема: Worker не запускается

**Решение**:
```bash
# Проверить логи
docker logs telegram-assistant-worker-1 2>&1 | tail -100

# Проверить синтаксис
python3 -m py_compile api/worker/services/ocr_enhancement_service.py
```

### Проблема: LLM не отвечает

**Решение**:
```bash
# Проверить доступность LLM
docker logs telegram-assistant-worker-1 2>&1 | grep -i "gigachat\|llm.*error\|failed.*initialize"

# Проверить переменные окружения
docker exec telegram-assistant-worker-1 env | grep -i "gigachat\|openai"
```

### Проблема: Entities не извлекаются

**Решение**:
```bash
# Проверить логи извлечения
docker logs telegram-assistant-worker-1 2>&1 | grep -A 5 "Entity extraction completed"

# Проверить ответ LLM
docker logs telegram-assistant-worker-1 2>&1 | grep -A 3 "LLM response received for entity extraction"
```

---

## Rollback (если нужно)

### Откат изменений

```bash
# 1. Вернуть старый промпт в ocr_enhancement_service.py
git checkout HEAD -- api/worker/services/ocr_enhancement_service.py

# 2. Перезапустить worker
docker restart telegram-assistant-worker-1

# 3. Проверить логи
docker logs telegram-assistant-worker-1 2>&1 | tail -50
```

---

## Выводы

1. ✅ **Изменения применены**: Промпт улучшен в коде
2. ⏭️ **Требуется перезапуск**: Worker нужно перезапустить
3. ⏭️ **Тестирование**: После перезапуска протестировать
4. ⏭️ **Мониторинг**: Отслеживать результаты

**Готово к применению!**

