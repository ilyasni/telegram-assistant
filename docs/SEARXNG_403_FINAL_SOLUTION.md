# SearXNG - Финальное решение проблемы 403 Forbidden

**Дата**: 2025-11-05  
**Статус**: ✅ Решение применено

## 🔍 Проблема

SearXNG возвращает `403 Forbidden` несмотря на:
- ✅ `limiter: false` в settings.yml
- ✅ `SEARXNG_LIMITER=false` в environment
- ✅ `public_instance: false`

**Ошибка в логах:**
```
ERROR:searx.botdetection: X-Forwarded-For nor X-Real-IP header is set!
```

## 🔍 Причина

Анализ кода SearXNG показал:

1. **botdetection инициализируется ВСЕГДА**, даже при `limiter: false`
   - См. `/usr/local/searxng/searx/limiter.py:212-214`:
   ```python
   # even if the limiter is not activated, the botdetection must be activated
   # (e.g. the self_info plugin uses the botdetection to get client IP)
   botdetection.init(cfg, valkey_client)
   ```

2. **botdetection использует trusted_proxies** для определения IP клиента
   - Если заголовки `X-Forwarded-For`/`X-Real-IP` не переданы или IP не в trusted_proxies/pass_ip, запрос блокируется
   - См. `/usr/local/searxng/searx/botdetection/trusted_proxies.py`

3. **Ошибка возникает в `filter_request()`** в `limiter.py`
   - Проверяет IP адрес против `pass_ip` и `block_ip` списков
   - Если IP не найден в `pass_ip`, запрос блокируется

## ✅ Решение

### 1. Создать правильный `limiter.toml`

Файл `searxng/limiter.toml` должен содержать:

```toml
[botdetection]
ipv4_prefix = 32
ipv6_prefix = 48
trusted_proxies = []

[botdetection.ip_limit]
filter_link_local = false
link_token = false

[botdetection.ip_lists]
block_ip = []
pass_ip = [
  "172.18.0.0/16",  # Docker подсеть
  "127.0.0.1/32",   # localhost
  "10.0.0.0/8",     # Частные сети
  "192.168.0.0/16", # Частные сети
]
pass_searxng_org = true
```

### 2. Настроить Docker подсеть

Определите подсеть Docker сети:
```bash
docker network inspect telegram-network | jq -r '.[0].IPAM.Config[0].Subnet'
```

Обновите `pass_ip` в `limiter.toml` с правильной подсетью.

### 3. Убедиться, что заголовки передаются

В `api/services/searxng_service.py` заголовки уже настроены:
```python
self.default_headers = {
    "X-Real-IP": container_ip,
    "X-Forwarded-For": container_ip,
    # ... другие заголовки
}
```

## 📋 Проверка

### Тест прямой доступ:
```bash
docker compose exec api python3 -c "
import httpx
import asyncio

async def test():
    async with httpx.AsyncClient(timeout=10.0) as client:
        headers = {
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json',
            'X-Forwarded-For': '172.18.0.15',
            'X-Real-IP': '172.18.0.15',
        }
        r = await client.get('http://searxng:8080/search?q=test&format=json', headers=headers)
        print(f'Status: {r.status_code}')

asyncio.run(test())
"
```

### Проверка логов:
```bash
docker compose logs searxng --tail 30 | grep -iE "botdetection|403|error"
```

## 🔗 Context7 Best Practices

1. ✅ **botdetection всегда активен** - нужно настроить `limiter.toml`
2. ✅ **trusted_proxies** - для определения IP клиента за прокси
3. ✅ **pass_ip** - список разрешенных IP/подсетей для Docker сети
4. ✅ **X-Forwarded-For/X-Real-IP** - обязательные заголовки для botdetection

## 📝 Ссылки

- [SearXNG Limiter Documentation](https://docs.searxng.org/admin/limiter.html)
- [SearXNG Bot Detection](https://docs.searxng.org/admin/bot-detection.html)
- [Context7 SearXNG](https://context7.com/searxng/searxng)

