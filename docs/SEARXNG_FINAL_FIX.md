# SearXNG - Final Fix (403 Forbidden)

**Дата**: 2025-02-02  
**Статус**: ✅ Исправлено

## ✅ Проблема

SearXNG возвращал 403 Forbidden из-за активного bot detection, несмотря на:
- ✅ `SEARXNG_LIMITER=false` в environment
- ✅ `limiter: false` в settings.yml
- ✅ `public_instance: false` в settings.yml
- ✅ User-Agent и заголовки в коде

## ✅ Решение

### 1. Создание limiter.toml (критично!)

Создан файл `./searxng/limiter.toml`:
```toml
[botdetection]
enabled = false
```

**Важно**: 
- Секция должна называться `[botdetection]` (не `[bot_detection]`)
- Файл должен находиться в `/etc/searxng/limiter.toml` (через volume mount)

### 2. BasicAuth и User-Agent (api/services/searxng_service.py)

Добавлена поддержка BasicAuth:
```python
auth = None
if settings.searxng_user and settings.searxng_password:
    auth = httpx.BasicAuth(
        settings.searxng_user,
        settings.searxng_password
    )
```

User-Agent обновлен:
```python
headers = {
    "User-Agent": "TelegramAssistant/3.1 (RAG Hybrid Search)"
}
```

### 3. Environment Variables (env.example)

Добавлены переменные для BasicAuth:
```bash
SEARXNG_USER=
SEARXNG_PASSWORD=
```

## 📝 Конфигурация

### Docker Compose
```yaml
searxng:
  volumes:
    - ./searxng:/etc/searxng  # Включает limiter.toml
  environment:
    - SEARXNG_LIMITER=false
    - SEARXNG_PUBLIC_INSTANCE=false
```

### limiter.toml (./searxng/limiter.toml)
```toml
[botdetection]
enabled = false
```

### SearXNG Service
- **BasicAuth**: Опционально (если SEARXNG_USER/SEARXNG_PASSWORD заданы)
- **User-Agent**: `TelegramAssistant/3.1 (RAG Hybrid Search)`
- **Headers**: Accept, X-Forwarded-For, X-Real-IP

## ✅ Проверка

```bash
# Тест через Python
docker compose exec api python3 -c "
import asyncio
from services.searxng_service import get_searxng_service

async def test():
    service = get_searxng_service()
    result = await service.search('Python', user_id='test')
    print(f'Results: {len(result.results)}')

asyncio.run(test())
"
```

## ✅ Итог

**SearXNG исправлен:**
- ✅ limiter.toml создан
- ✅ BasicAuth поддержка добавлена
- ✅ User-Agent обновлен
- ✅ Bot detection отключен

**Система готова к работе.**

