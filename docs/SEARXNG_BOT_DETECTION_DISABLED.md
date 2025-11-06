# SearXNG - Bot Detection Disabled

**Дата**: 2025-02-02  
**Статус**: ✅ Bot detection отключен

## ✅ Решение (Context7 Best Practices)

### Проблема
SearXNG возвращал 403 Forbidden из-за активного bot detection, несмотря на `limiter: false` в settings.yml.

### Решение

#### 1. Settings.yml (searxng/settings.yml)
```yaml
server:
  limiter: false
  public_instance: false
  method: "GET"
```

#### 2. Environment Variables (docker-compose.yml)
```yaml
environment:
  - SEARXNG_LIMITER=false
  - SEARXNG_PUBLIC_INSTANCE=false
```

#### 3. Limiter.toml (критично!)
Создан файл `/etc/searxng/limiter.toml`:
```toml
[botdetection]
enabled = false

[limiter]
enabled = false
```

**Важно**: 
- Обе секции `[botdetection]` и `[limiter]` должны быть с `enabled = false`
- Файл должен находиться в `/etc/searxng/limiter.toml` (через volume mount)

## 📝 Конфигурация

### Docker Compose
```yaml
searxng:
  environment:
    - SEARXNG_LIMITER=false
    - SEARXNG_PUBLIC_INSTANCE=false
  volumes:
    - ./searxng:/etc/searxng  # Включает limiter.toml
```

### limiter.toml (./searxng/limiter.toml)
```toml
[botdetection]
enabled = false

[limiter]
enabled = false
```

### settings.yml (./searxng/settings.yml)
```yaml
server:
  limiter: false
  public_instance: false
  method: "GET"
```

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

# Прямой curl
curl "http://localhost:8080/search?q=test&format=json"
```

## ✅ Итог

**Bot detection полностью отключен:**
- ✅ limiter.toml создан с правильным форматом
- ✅ settings.yml настроен
- ✅ Environment variables применены
- ✅ SearXNG работает без 403 Forbidden

**Система готова к работе.**

