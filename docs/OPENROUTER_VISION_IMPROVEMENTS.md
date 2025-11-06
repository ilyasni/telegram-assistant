# Улучшения OpenRouter Vision Adapter

**Дата**: 2025-11-06  
**Статус**: ✅ Реализовано

## Context

Улучшена обработка ошибок и добавлен circuit breaker в OpenRouter Vision адаптер для повышения надёжности и устойчивости системы.

## Plan

1. ✅ Добавлен Circuit Breaker для защиты от каскадных сбоев
2. ✅ Улучшена обработка rate limits с использованием заголовков API
3. ✅ Добавлена обработка quota exhausted для free моделей
4. ✅ Улучшен exponential backoff с jitter
5. ✅ Обновлена документация

## Patch

### 1. Circuit Breaker

**Файл**: `worker/ai_adapters/openrouter_vision.py`

Добавлен circuit breaker для защиты от каскадных сбоев:
- Автоматическое создание при инициализации
- Настраиваемые параметры через переменные окружения
- Интеграция с Prometheus метриками

**Конфигурация**:
```python
OPENROUTER_CIRCUIT_BREAKER_FAILURE_THRESHOLD=5  # По умолчанию
OPENROUTER_CIRCUIT_BREAKER_RECOVERY_TIMEOUT=60  # По умолчанию
```

### 2. Улучшенная обработка Rate Limits

**Context7 Best Practice**: Использование заголовков API для точного определения времени ожидания

**Улучшения**:
- Использование заголовка `Retry-After` (секунды)
- Использование заголовка `X-RateLimit-Reset` (timestamp в миллисекундах)
- Обработка quota exhausted для free моделей (не повторяем запросы)
- Exponential backoff с jitter как fallback

**Пример обработки**:
```python
if e.response.status_code == 429:
    # Проверяем Retry-After
    if "Retry-After" in headers:
        retry_after = int(headers["Retry-After"])
    # Проверяем X-RateLimit-Reset
    elif "X-RateLimit-Reset" in headers:
        reset_timestamp = int(headers["X-RateLimit-Reset"]) / 1000
        retry_after = max(0, reset_timestamp - time.time())
    
    # Если free quota exhausted - не повторяем
    if "free-models-per-day" in error_body:
        raise Exception("Free quota exhausted")
```

### 3. Exponential Backoff с Jitter

**Context7 Best Practice**: Jitter предотвращает thundering herd problem

**Реализация**:
```python
import random
base_delay = 2 ** attempt  # Exponential: 1s, 2s, 4s, 8s...
jitter = random.uniform(0, 0.3 * base_delay)  # До 30% jitter
wait_time = base_delay + jitter
```

### 4. Обработка Quota Exhausted

**Проблема**: Free модели имеют дневной лимит запросов

**Решение**: При получении ошибки `free-models-per-day` не повторяем запросы, сразу возвращаем ошибку

```python
error_code = error_body.get("error", {}).get("code", "")
if "free-models-per-day" in str(error_body) or error_code == "free_quota_exceeded":
    logger.warning("OpenRouter free quota exhausted, not retrying")
    raise Exception("OpenRouter free quota exhausted")
```

## Checks

### Проверка Circuit Breaker

```bash
# Проверить метрики circuit breaker
curl http://localhost:8000/metrics | grep circuit_breaker

# Должны быть метрики:
# circuit_breaker_state{name="openrouter_vision",state="closed"} 0.0
# circuit_breaker_calls_total{name="openrouter_vision",result="success"} ...
```

### Проверка обработки Rate Limits

```bash
# Проверить логи на обработку rate limits
docker compose logs worker | grep -i "rate limit"

# Должны быть логи:
# OpenRouter rate limit, retrying attempt=1 wait_seconds=2 retry_after=60
```

## Impact / Rollback

### Преимущества

1. **Защита от каскадных сбоев**: Circuit breaker предотвращает перегрузку при множественных сбоях
2. **Точная обработка rate limits**: Использование заголовков API для оптимального времени ожидания
3. **Экономия ресурсов**: Не повторяем запросы при quota exhausted
4. **Предотвращение thundering herd**: Jitter в exponential backoff

### Обратная совместимость

- ✅ Все изменения обратно совместимы
- ✅ Circuit breaker опционален (можно передать свой экземпляр)
- ✅ Стандартные значения параметров работают из коробки

### Rollback

Если нужно откатить изменения:
1. Удалить circuit breaker из `__init__`
2. Вернуть простую обработку rate limits без заголовков
3. Убрать обработку quota exhausted

## Best Practices (Context7)

### 1. Circuit Breaker Pattern

- ✅ Автоматическое управление состоянием
- ✅ Метрики Prometheus для мониторинга
- ✅ Защита от каскадных сбоев

### 2. Rate Limit Handling

- ✅ Использование заголовков API (`Retry-After`, `X-RateLimit-Reset`)
- ✅ Exponential backoff с jitter
- ✅ Обработка различных типов rate limits

### 3. Error Handling

- ✅ Разделение retryable и non-retryable ошибок
- ✅ Детальное логирование для диагностики
- ✅ Graceful degradation

## Выводы

1. ✅ **Circuit Breaker добавлен**: Защита от каскадных сбоев реализована
2. ✅ **Rate limits улучшены**: Использование заголовков API для точного времени ожидания
3. ✅ **Quota exhausted обработан**: Не повторяем запросы при исчерпании free quota
4. ✅ **Exponential backoff улучшен**: Добавлен jitter для предотвращения thundering herd

## Следующие шаги

1. Мониторить метрики circuit breaker в Prometheus
2. Проверить логи на корректную обработку rate limits
3. Настроить алерты на открытие circuit breaker
4. Рассмотреть использование платных моделей при исчерпании free quota

