# SearXNG - Complete Fix (Final)

**Дата**: 2025-02-02  
**Статус**: ✅ SearXNG полностью настроен

## ✅ Решение проблемы 403 Forbidden

### Проблема
SearXNG возвращал 403 Forbidden из-за активного bot detection.

### Решение (Context7 Best Practices)

#### 1. Переменные окружения (docker-compose.yml)
```yaml
environment:
  - SEARXNG_LIMITER=false
  - SEARXNG_PUBLIC_INSTANCE=false
```

#### 2. Settings.yml
Автоматически создается SearXNG с настройками:
- `limiter: false`
- `public_instance: false`
- `bind_address: "0.0.0.0"`

#### 3. Limiter.toml (критично!)
Создан файл `./searxng/limiter.toml`:
```toml
# Context7: Отключаем bot detection для внутреннего использования
# Согласно документации SearXNG: https://docs.searxng.org/admin/limiter.html

[botdetection]
enabled = false
```

**Важно**: Секция должна называться `[botdetection]` (не `[bot_detection]`), параметр `enabled = false`.

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

#### limiter.toml (./searxng/limiter.toml)
```toml
[botdetection]
enabled = false
```

#### SearXNG Service
- **Headers**: User-Agent, X-Forwarded-For, X-Real-IP
- **Category**: `general`
- **Caching**: Redis (TTL 3600s)

## 🔧 Интеграция с Caddy

### Текущая конфигурация
**Caddy НЕ требуется для SearXNG**, так как:
- ✅ SearXNG используется только внутри Docker network
- ✅ API обращается напрямую: `http://searxng:8080`
- ✅ Нет необходимости в HTTPS для внутренних запросов

### Опциональная настройка Caddy
Если нужно публичное HTTPS API для SearXNG, можно добавить в `Caddyfile`:
```caddy
searxng.produman.studio {
    tls {$CADDY_TLS_EMAIL}
    
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
        X-Content-Type-Options nosniff
        -Server
    }
    
    # Передаем заголовки для обхода bot detection
    reverse_proxy searxng:8080 {
        header_up X-Real-IP {remote_host}
        header_up X-Forwarded-For {remote_host}
        header_up X-Forwarded-Proto {scheme}
    }
}
```

**Рекомендация**: Не добавлять в Caddy, так как SearXNG используется только внутри сети.

## ✅ Best Practices (Context7)

1. ✅ **Тройная настройка**: limiter.toml + env vars + settings.yml
2. ✅ **Правильный формат**: `[botdetection]` (не `[bot_detection]`)
3. ✅ **Internal Use**: Отключен для внутреннего использования
4. ✅ **Network Isolation**: Только внутри Docker network
5. ✅ **Caddy**: Не требуется для внутреннего использования
6. ✅ **Health Checks**: Docker healthcheck настроен

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
- ✅ Bot detection отключен через limiter.toml
- ✅ Поиск работает корректно
- ✅ Интеграция с RAG Service активна
- ✅ Caddy не требуется (внутреннее использование)

**Система готова к использованию.**

### Источники
- [SearXNG Documentation - Limiter](https://docs.searxng.org/admin/limiter.html)
- [SearXNG Bot Detection Configuration](https://docs.searxng.org/admin/searx.limiter.html)

