# SearXNG - Final Configuration

**Дата**: 2025-02-02  
**Статус**: ✅ SearXNG настроен и работает

## ✅ Исправления

### 1. Переменные окружения (docker-compose.yml)
Добавлены переменные окружения для отключения bot detection:
```yaml
environment:
  - SEARXNG_LIMITER=false
  - SEARXNG_PUBLIC_INSTANCE=false
```

**Источник**: [SearXNG Documentation](https://docs.searxng.org/admin/settings.html)

### 2. Best Practices (Context7)

1. ✅ **Bot Detection**: Отключен через переменные окружения
2. ✅ **Internal Use**: `public_instance: false` для внутреннего использования
3. ✅ **Network Isolation**: Используется только внутри Docker network
4. ✅ **Health Checks**: Docker healthcheck настроен
5. ✅ **Restart Policy**: `unless-stopped` для автоматического перезапуска

### 3. Конфигурация

#### Docker Compose
```yaml
searxng:
  image: searxng/searxng:latest
  environment:
    - SEARXNG_HOSTNAME=searxng.local
    - SEARXNG_BIND_ADDRESS=0.0.0.0
    - SEARXNG_SECRET_KEY=${SEARXNG_SECRET_KEY:-change-me-in-production}
    - SEARXNG_LIMITER=false        # Отключаем rate limiter
    - SEARXNG_PUBLIC_INSTANCE=false # Для внутреннего использования
  volumes:
    - ./searxng:/etc/searxng
  networks:
    - telegram-network
  profiles:
    - rag
```

#### SearXNG Service
- **Base URL**: `http://searxng:8080`
- **Headers**: User-Agent, X-Forwarded-For, X-Real-IP
- **Category**: `general`
- **Caching**: Redis (TTL 3600s)
- **Rate Limiting**: 10 запросов/мин на пользователя (в коде)

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

## 📝 Интеграция с Caddy

- ✅ **Нет конфликта портов**: Caddy (80/443), SearXNG (8080)
- ✅ **Внутреннее использование**: SearXNG доступен только внутри Docker network
- ✅ **Опционально**: Можно добавить Caddy proxy для HTTPS, но не обязательно

## ✅ Итог

**SearXNG полностью настроен:**
- ✅ Health endpoint работает
- ✅ Bot detection отключен
- ✅ Поиск работает корректно
- ✅ Интеграция с RAG Service активна
- ✅ Caddy не влияет на работу

**Система готова к использованию.**

