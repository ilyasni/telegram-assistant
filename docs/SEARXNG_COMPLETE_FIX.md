# SearXNG - Complete Fix

**Дата**: 2025-02-02  
**Статус**: ✅ SearXNG полностью настроен

## ✅ Полное решение проблемы 403 Forbidden

### Проблема
SearXNG возвращал 403 Forbidden из-за активного bot detection, несмотря на переменные окружения.

### Решение

#### 1. Переменные окружения (docker-compose.yml)
```yaml
environment:
  - SEARXNG_LIMITER=false
  - SEARXNG_PUBLIC_INSTANCE=false
```

#### 2. Создание limiter.toml (критично!)
Создан файл `/etc/searxng/limiter.toml` для полного отключения bot detection:
```toml
# Context7: Отключаем bot detection для внутреннего использования

[bot_detection]
enabled = false

[limiter]
enabled = false
```

**Важно**: Переменные окружения не всегда применяются, поэтому создание `limiter.toml` является обязательным шагом.

### 3. Best Practices (Context7)

1. ✅ **Двойная настройка**: Переменные окружения + limiter.toml
2. ✅ **Internal Use**: Отключен для внутреннего использования
3. ✅ **Network Isolation**: Только внутри Docker network
4. ✅ **Headers**: X-Forwarded-For, X-Real-IP добавлены в код
5. ✅ **Health Checks**: Docker healthcheck настроен

### 4. Финальная конфигурация

#### Docker Compose
```yaml
searxng:
  image: searxng/searxng:latest
  environment:
    - SEARXNG_HOSTNAME=searxng.local
    - SEARXNG_BIND_ADDRESS=0.0.0.0
    - SEARXNG_SECRET_KEY=${SEARXNG_SECRET_KEY:-change-me-in-production}
    - SEARXNG_LIMITER=false
    - SEARXNG_PUBLIC_INSTANCE=false
  volumes:
    - ./searxng:/etc/searxng
```

#### limiter.toml (в ./searxng/limiter.toml)
```toml
[bot_detection]
enabled = false

[limiter]
enabled = false
```

#### SearXNG Service
- **Headers**: User-Agent, X-Forwarded-For, X-Real-IP
- **Category**: `general`
- **Caching**: Redis (TTL 3600s)

## 🔍 Проверка

### Health Check
```bash
curl http://localhost:8080/healthz
# Ответ: OK
```

### Test Search
```bash
# Прямой поиск
curl "http://localhost:8080/search?q=test&format=json&categories=general" \
  -H "User-Agent: Mozilla/5.0" \
  -H "X-Forwarded-For: 127.0.0.1" \
  -H "X-Real-IP: 127.0.0.1"

# Через SearXNG Service
docker compose exec api python3 -c "
from services.searxng_service import get_searxng_service
import asyncio
async def test():
    service = get_searxng_service()
    result = await service.search('test', user_id='test', lang='ru')
    print(f'Results: {len(result.results)}')
asyncio.run(test())
"
```

## 📝 Интеграция с Caddy

- ✅ **Нет конфликта**: Caddy (80/443), SearXNG (8080)
- ✅ **Внутреннее использование**: SearXNG только внутри Docker network
- ✅ **Не требует проксирования**: Работает напрямую

## ✅ Итог

**SearXNG полностью настроен:**
- ✅ Health endpoint работает
- ✅ Bot detection отключен (limiter.toml + env vars)
- ✅ Поиск работает корректно
- ✅ Интеграция с RAG Service активна
- ✅ Caddy не влияет на работу

**Система готова к использованию.**

