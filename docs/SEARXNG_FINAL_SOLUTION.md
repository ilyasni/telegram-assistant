# SearXNG - Финальное решение проблемы 403

**Дата**: 2025-02-02

## ❌ Проблема

SearXNG возвращает `403 Forbidden` даже при:
- `server.limiter: false` в settings.yml
- `SEARXNG_LIMITER=false` в docker-compose.yml
- `botdetection.ip_lists.pass_ip: ["0.0.0.0/0"]` в settings.yml
- Заголовки `X-Forwarded-For` и `X-Real-IP` передаются

## 🔍 Анализ

Проблема в том, что bot detection в SearXNG проверяет **реальный IP соединения** (source IP), а не заголовки. В Docker сети IP контейнера `api` (172.18.0.15) не попадает в whitelist, даже если `pass_ip: ["0.0.0.0/0"]`.

## ✅ Решение

### Вариант 1: Использовать реальный IP контейнера (рекомендуется)

1. Определить IP контейнера `api`:
```bash
docker compose exec api hostname -i | awk '{print $1}'
# Результат: 172.18.0.15
```

2. Обновить `settings.yml`:
```yaml
botdetection:
  ip_lists:
    pass_ip:
      - "172.18.0.15/32"    # IP контейнера api
      - "172.18.0.0/16"      # Вся Docker подсеть
      - "127.0.0.1/32"       # localhost
```

### Вариант 2: Использовать подсеть Docker

Если подсеть известна (например, `172.18.0.0/16`):
```yaml
botdetection:
  ip_lists:
    pass_ip:
      - "172.18.0.0/16"      # Docker подсеть
      - "127.0.0.1/32"       # localhost
```

### Вариант 3: Отключить bot detection через переменные окружения

Добавить в `docker-compose.yml`:
```yaml
environment:
  - SEARXNG_BOT_DETECTION=false  # Если поддерживается
```

## 📋 Текущая конфигурация

### docker-compose.yml
```yaml
environment:
  - SEARXNG_LIMITER=false
  - SEARXNG_PUBLIC_INSTANCE=false
  - SEARXNG_SETTINGS_PATH=/etc/searxng/settings.yml
```

### settings.yml
```yaml
server:
  limiter: false
  public_instance: false

botdetection:
  ip_lists:
    pass_ip:
      - "0.0.0.0/0"       # Разрешаем все IP
      - "127.0.0.1/32"    # localhost
```

## ⚠️ Важно

- Bot detection проверяет **source IP**, а не заголовки `X-Forwarded-For`/`X-Real-IP`
- `pass_ip: ["0.0.0.0/0"]` может не работать в некоторых версиях SearXNG
- Рекомендуется указать конкретную подсеть Docker или IP контейнера

## ✅ Проверка

```bash
# 1. Определить IP контейнера
docker compose exec api hostname -i

# 2. Обновить settings.yml с конкретным IP/подсетью

# 3. Перезапустить SearXNG
docker compose --profile rag restart searxng

# 4. Тест
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
