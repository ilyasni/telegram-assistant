# SearXNG - Примененные исправления

**Дата**: 2025-11-06  
**Статус**: ✅ Все исправления применены и протестированы

## Исправленные проблемы

### 1. Сериализация datetime в кэше Redis

**Проблема:**
```
ERROR: Error saving to cache: Object of type datetime is not JSON serializable
```

**Решение (Context7 Best Practices):**
- Добавлен `json_encoders` в `SearXNGResult.Config` для автоматической сериализации datetime в ISO формат
- Использован `model_dump(mode='json')` вместо `model_dump()` для применения json_encoders

**Изменения:**
```python
class SearXNGResult(BaseModel):
    # ... поля ...
    
    class Config:
        """Context7: JSON encoders для корректной сериализации datetime."""
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

async def _save_to_cache(self, cache_key: str, results: List[SearXNGResult]) -> None:
    # Context7: Используем mode='json' для автоматической сериализации datetime
    data = [result.model_dump(mode='json') for result in results]
    self.redis_client.setex(cache_key, self.cache_ttl, json.dumps(data))
```

**Файл:** `api/services/searxng_service.py`

### 2. Health Check статус "unhealthy"

**Проблема:**
- Контейнер показывал статус `unhealthy` из-за отсутствия `curl` в официальном образе SearXNG

**Решение (Context7 Best Practices):**
- Заменен `curl` на `wget` в health check команде
- `wget` присутствует в официальном образе SearXNG

**Изменения:**
```yaml
healthcheck:
  # Context7: Health check использует wget вместо curl (curl отсутствует в официальном образе)
  test: ["CMD-SHELL", "wget --no-verbose --tries=1 --spider http://localhost:8080/healthz || exit 1"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 10s
```

**Файл:** `docker-compose.yml`

## Результаты тестирования

### Тест 1: Кэширование
```
✅ Кэширование работает без ошибок!
First search - Results: 2
Second search (cached) - Results: 2
```
- Нет ошибок сериализации datetime
- Кэш сохраняется и читается корректно

### Тест 2: Health Check
```
STATUS: Up 47 seconds (healthy)
```
- Контейнер показывает статус `healthy`
- Health check endpoint работает корректно

## Best Practices (Context7)

1. **Сериализация datetime:**
   - Использовать `json_encoders` в Pydantic моделях
   - Использовать `model_dump(mode='json')` для автоматического применения encoders
   - ISO формат для datetime обеспечивает совместимость и читаемость

2. **Health Checks:**
   - Использовать инструменты, доступные в официальном образе
   - `wget` более универсален, чем `curl` в минимальных образах
   - Проверять наличие инструментов перед использованием

3. **Кэширование:**
   - Всегда сериализовать сложные типы (datetime, UUID) в JSON-совместимые форматы
   - Использовать Pydantic для валидации и сериализации
   - Логировать ошибки кэширования для диагностики

## Интеграция в сервисы

Все исправления интегрированы в:
- ✅ `api/services/searxng_service.py` - исправлена сериализация datetime
- ✅ `docker-compose.yml` - исправлен health check
- ✅ Протестировано и работает в продакшене

## Проверка

```bash
# Проверка health check
docker compose ps searxng
# Должно быть: (healthy)

# Проверка кэширования
docker compose exec -T api python3 -c "
from services.searxng_service import get_searxng_service
from redis import Redis
import asyncio

async def test():
    redis_client = Redis(host='redis', port=6379, db=0, decode_responses=True)
    service = get_searxng_service(redis_client=redis_client)
    result = await service.search('test', user_id='test', lang='ru')
    print(f'Results: {len(result.results)}')
    await service.close()

asyncio.run(test())
"
# Должно работать без ошибок сериализации
```

## Ссылки

- [Pydantic JSON Encoders](https://docs.pydantic.dev/latest/concepts/serialization/#custom-serializers)
- [SearXNG Docker Health Checks](https://docs.searxng.org/admin/installation-docker.html)
- [Context7 SearXNG](https://context7.com/searxng/searxng)

