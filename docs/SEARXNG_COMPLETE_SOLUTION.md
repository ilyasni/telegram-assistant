# SearXNG - Complete Solution

**Дата**: 2025-02-02  
**Статус**: ✅ SearXNG настроен и работает

## ✅ Финальное решение

### Проблема
SearXNG возвращал 403 Forbidden из-за активного bot detection.

### Решение (Context7 Best Practices)

#### 1. Переменные окружения (docker-compose.yml)
```yaml
environment:
  - SEARXNG_LIMITER=false
  - SEARXNG_PUBLIC_INSTANCE=false
```

#### 2. Settings.yml (автоматически создается)
Настройки применяются через переменные окружения:
- `limiter: false`
- `public_instance: false`
- `bind_address: "0.0.0.0"`
- `method: "GET"` (меняется в settings.yml для обхода bot detection)

#### 3. SearXNG Service (api/services/searxng_service.py)
- **Метод**: GET (соответствует settings.yml)
- **Headers**: User-Agent, X-Forwarded-For, X-Real-IP, Accept
- **Category**: `general`
- **Caching**: Redis (TTL 3600s)

### Финальная конфигурация

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
  profiles:
    - rag
```

#### Settings.yml (автоматически)
```yaml
server:
  limiter: false
  public_instance: false
  bind_address: "0.0.0.0"
  method: "GET"  # Важно: GET менее строгий для bot detection
```

#### SearXNG Service
- **URL**: `http://searxng:8080/search`
- **Method**: GET
- **Headers**: User-Agent, X-Forwarded-For, X-Real-IP, Accept
- **Parameters**: q, format, categories, language, pageno

## 🔧 Интеграция с Caddy

### Текущая конфигурация
**Caddy НЕ требуется для SearXNG:**
- ✅ SearXNG используется только внутри Docker network
- ✅ API обращается напрямую: `http://searxng:8080`
- ✅ Нет необходимости в HTTPS для внутренних запросов

### Опциональная настройка (закомментирована в Caddyfile)
Если нужно публичное HTTPS API для SearXNG, можно раскомментировать блок в `Caddyfile`:
```caddy
# searxng.produman.studio {
#     tls {$CADDY_TLS_EMAIL}
#     reverse_proxy searxng:8080 {
#         header_up X-Real-IP {remote_host}
#         header_up X-Forwarded-For {remote_host}
#     }
# }
```

**Рекомендация**: Не использовать, так как SearXNG доступен только внутри сети.

## ✅ Best Practices (Context7)

1. ✅ **Метод GET**: Менее строгий для bot detection
2. ✅ **Переменные окружения**: SEARXNG_LIMITER=false, SEARXNG_PUBLIC_INSTANCE=false
3. ✅ **Settings.yml**: Автоматически создается с правильными настройками
4. ✅ **Headers**: X-Forwarded-For, X-Real-IP для обхода bot detection
5. ✅ **Network Isolation**: Только внутри Docker network
6. ✅ **Caddy**: Не требуется для внутреннего использования

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
  -H "X-Forwarded-For: 127.0.0.1"

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

## ✅ Итог

**SearXNG полностью настроен:**
- ✅ Health endpoint работает
- ✅ Bot detection отключен (limiter=false, method=GET)
- ✅ Поиск работает корректно
- ✅ Интеграция с RAG Service активна
- ✅ Caddy не требует настройки

**Система готова к использованию.**

### Источники
- [SearXNG Documentation - Settings](https://docs.searxng.org/admin/settings.html)
- [SearXNG Bot Detection Configuration](https://docs.searxng.org/admin/searx.limiter.html)

