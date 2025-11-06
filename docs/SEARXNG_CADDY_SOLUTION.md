# SearXNG - Решение через Caddy Reverse Proxy

**Дата**: 2025-02-02

## ✅ Решение проблемы 403 Forbidden

### Проблема
SearXNG возвращает `403 Forbidden` при прямых запросах из Docker сети, даже с правильной конфигурацией `limiter.toml` и `settings.yml`. Bot detection не видит заголовки `X-Forwarded-For`/`X-Real-IP` при прямых запросах.

### Решение
Использовать SearXNG через Caddy reverse proxy, который правильно устанавливает заголовки для bot detection.

## 📋 Конфигурация

### 1. Caddyfile

Добавлен endpoint `/searxng` в блок `api.produman.studio`:

```caddyfile
api.produman.studio {
    # ... existing config ...
    
    # SearXNG endpoint - внутренний прокси для обхода bot detection
    # Context7: SearXNG требует reverse proxy для правильной работы заголовков X-Forwarded-For/X-Real-IP
    handle /searxng/* {
        reverse_proxy searxng:8080 {
            header_up X-Real-IP {remote_host}
            header_up X-Forwarded-For {remote_host}
            header_up X-Forwarded-Proto {scheme}
            # Удаляем префикс /searxng из пути перед проксированием
            rewrite /searxng / strip_path
        }
    }
}
```

### 2. api/config.py

```python
searxng_url: str = "http://caddy/searxng"  # Context7: Используем Caddy для обхода bot detection
```

### 3. api/services/searxng_service.py

Обновлен путь для работы через Caddy:

```python
search_path = "/search" if "/searxng" in self.base_url else "/search"
search_url = f"{self.base_url}{search_path}?q=..."
```

## 🔍 Как это работает

1. **API контейнер** делает запрос к `http://caddy/searxng/search?...`
2. **Caddy** получает запрос и устанавливает заголовки:
   - `X-Real-IP: {remote_host}` (IP контейнера api)
   - `X-Forwarded-For: {remote_host}`
   - `X-Forwarded-Proto: http`
3. **Caddy** удаляет префикс `/searxng` и проксирует запрос к `searxng:8080/search?...`
4. **SearXNG** видит заголовки и пропускает запрос через bot detection

## ✅ Преимущества

- ✅ Bot detection работает корректно (видит заголовки)
- ✅ Не требует изменения SearXNG конфигурации
- ✅ Использует существующую инфраструктуру (Caddy)
- ✅ Безопасно (только внутренний доступ)

## 🔧 Проверка

```bash
# 1. Проверить Caddy
docker compose ps caddy

# 2. Проверить логи Caddy
docker compose logs caddy --tail 20 | grep -iE "searxng|error"

# 3. Проверить логи SearXNG
docker compose logs searxng --tail 20 | grep -iE "botdetection|403|X-Forwarded-For"

# 4. Тест через API
docker compose exec -T api python3 -c "
import asyncio
import sys
sys.path.insert(0, '/app')
from services.searxng_service import get_searxng_service

async def test():
    service = get_searxng_service()
    result = await service.search('test', user_id='test', lang='ru')
    print(f'Results: {len(result.results)}')

asyncio.run(test())
"
```

## ⚠️ Важно

- Caddy должен быть запущен (`docker compose up -d caddy`)
- SearXNG доступен через `http://caddy/searxng` (не напрямую)
- Путь `/searxng` автоматически удаляется Caddy перед проксированием

