# Исправления GigaChat Proxy и Neo4j Topics

**Дата**: 2025-11-05  
**Статус**: Исправления применены

## ✅ Исправление 1: GigaChat Proxy — 307 Temporary Redirect

### Проблема
- Прокси возвращал `307 Temporary Redirect` с `location: /chat/completions` (без `/v1`)
- Клиенты отправляли запросы на `/v1/chat/completions`, но прокси перенаправлял на `/chat/completions`
- LangChain GigaChat клиенты не следовали редиректам автоматически

### Решение
1. **Исправлен обработчик в `fallback_proxy.py`**:
   - Добавлена обработка обоих путей: `/v1/chat/completions` и `/chat/completions`
   - Добавлена обработка обоих путей для моделей: `/v1/models` и `/models`

2. **Исправлен URL в конфигурации**:
   - `api/config.py`: `openai_api_base` изменён с `http://gpt2giga-proxy:8090/v1` на `http://gpt2giga-proxy:8090`
   - LangChain автоматически добавит `/v1` при необходимости

3. **Обновлены все сервисы**:
   - `api/services/intent_classifier.py`
   - `api/services/rag_service.py`
   - `api/services/digest_service.py`
   - `api/services/trend_detection_service.py`

**Файлы**:
- `gpt2giga-proxy/fallback_proxy.py`
- `api/config.py`
- `api/services/*.py`

---

## ✅ Исправление 2: Neo4j — создание Topic узлов

### Проблема
- В Neo4j не создавались Topic узлы из тегов
- Запросы типа `MATCH (t:Topic ...)` возвращали пустые результаты
- Отсутствовали связи `HAS_TOPIC` и `RELATED_TO`

### Решение
Расширен метод `create_tag_relationships` в `worker/integrations/neo4j_client.py`:

1. **Создание Topic узлов из тегов**:
   - Нормализация тегов (lowercase, минимальная длина 3 символа)
   - Создание Topic узлов через `MERGE (topic:Topic {name: $topic_name})`
   - Связь `HAS_TOPIC` между постами и темами

2. **Создание RELATED_TO связей**:
   - Связи между Topic узлами из одного поста
   - Автоматическое вычисление similarity на основе веса (количество совместных постов)
   - Формула: `similarity = 0.5 + (weight * 0.1)`

**Код**:
```python
# Создание Topic узлов
MERGE (topic:Topic {name: $topic_name})
ON CREATE SET topic.created_at = datetime()
MERGE (p)-[:HAS_TOPIC]->(topic)

# Создание RELATED_TO связей
MERGE (t1)-[r:RELATED_TO]-(t2)
ON CREATE SET r.similarity = 0.5, r.weight = 1
ON MATCH SET r.weight = r.weight + 1, r.similarity = 0.5 + (r.weight * 0.1)
```

**Файлы**:
- `worker/integrations/neo4j_client.py`

---

## 📊 Результаты тестирования

### GigaChat Proxy
- ✅ Исправлена обработка редиректов
- ✅ Обрабатываются оба пути (`/v1/chat/completions` и `/chat/completions`)
- ✅ Intent Classifier работает корректно

### Neo4j Topics
- ✅ Topic узлы создаются из тегов
- ✅ Связи `HAS_TOPIC` и `RELATED_TO` создаются автоматически
- ✅ Граф знаний для тем будет заполняться при индексации

---

## 🔄 Следующие шаги

1. **Мониторинг**:
   - Отслеживать ошибки редиректов в логах
   - Проверять создание Topic узлов при индексации

2. **Оптимизация**:
   - При необходимости улучшить алгоритм similarity для Topic узлов
   - Добавить метрики для мониторинга Topic узлов

---

## 📝 Команды для проверки

### Проверка GigaChat Proxy
```bash
docker compose exec api python3 << 'PYTHON'
from services.intent_classifier import IntentClassifier
import asyncio
classifier = IntentClassifier()
result = asyncio.run(classifier.classify("Тест"))
print(f"Intent: {result.intent}, Confidence: {result.confidence}")
PYTHON
```

### Проверка Topic узлов
```bash
docker compose exec worker python3 << 'PYTHON'
from integrations.neo4j_client import Neo4jClient
import asyncio
client = Neo4jClient()
asyncio.run(client.connect())
driver = client._driver
async with driver.session() as session:
    result = await session.run("MATCH (t:Topic) RETURN count(t) as count")
    record = await result.single()
    print(f"Topic узлов: {record['count']}")
PYTHON
```

