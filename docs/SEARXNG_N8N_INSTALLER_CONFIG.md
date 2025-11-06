# SearXNG - Конфигурация на основе n8n-installer

**Дата**: 2025-02-02  
**Источник**: https://github.com/kossakovsky/n8n-installer  
**Статус**: ✅ Применено

## ✅ Применённые изменения (Context7 Best Practices)

### 1. Caddyfile - Полный домен вместо пути

**Было:**
```caddyfile
handle /searxng/* {
    reverse_proxy searxng:8080 {
        header_up X-Real-IP {remote_host}  # Неправильный синтаксис
        rewrite /searxng / strip_path
    }
}
```

**Стало:**
```caddyfile
searxng.produman.studio {
    tls {$CADDY_TLS_EMAIL}
    
    # BasicAuth только для внешних IP (опционально)
    @protected not remote_ip 127.0.0.0/8 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10
    
    # Security headers
    header {
        Content-Security-Policy "..."
        Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
        X-Content-Type-Options "nosniff"
        -Server
    }
    
    # Правильный синтаксис Caddy
    reverse_proxy searxng:8080 {
        header_up X-Forwarded-Port {http.request.port}
        header_up X-Real-IP {http.request.remote.host}  # Правильный синтаксис!
        header_up X-Forwarded-For {http.request.remote.host}
        header_up Connection "close"
    }
}
```

### 2. Docker Compose - SEARXNG_BASE_URL

**Критичное изменение:**
```yaml
environment:
  - SEARXNG_BASE_URL=https://searxng.produman.studio/  # КРИТИЧНО!
  - SEARXNG_HOSTNAME=searxng.produman.studio
  - UWSGI_WORKERS=4
  - UWSGI_THREADS=4
```

**Почему это важно:**
- SearXNG использует `SEARXNG_BASE_URL` для генерации ссылок и проверки запросов
- Без этого bot detection может работать неправильно

### 3. API Config - HTTPS URL

**Было:**
```python
searxng_url: str = "http://searxng:8080"
```

**Стало:**
```python
searxng_url: str = "https://searxng.produman.studio"
```

### 4. Settings.yml - use_default_settings

**Добавлено:**
```yaml
use_default_settings: true
server:
  limiter: false
  public_instance: false
  image_proxy: true
```

## 🔍 Ключевые отличия от n8n-installer

1. **BasicAuth**: Опционально (закомментировано), можно включить для защиты внешнего доступа
2. **Домен**: Используется `searxng.produman.studio` вместо переменной
3. **uWSGI**: Настройки workers/threads добавлены для оптимизации

## ✅ Преимущества новой конфигурации

1. ✅ **Правильный синтаксис Caddy** - `{http.request.remote.host}` вместо `{remote_host}`
2. ✅ **SEARXNG_BASE_URL** - SearXNG правильно определяет свой URL
3. ✅ **HTTPS** - Все запросы через HTTPS с автоматическим TLS
4. ✅ **Security headers** - Полный набор заголовков безопасности
5. ✅ **Cache policy** - Оптимизированное кэширование для разных типов контента
6. ✅ **Bot detection** - Должен работать корректно через reverse proxy

## 📋 Проверка

```bash
# 1. Проверить Caddy
docker compose logs caddy --tail 20 | grep searxng

# 2. Проверить SearXNG
docker compose logs searxng --tail 20 | grep -iE "base_url|listening|error"

# 3. Тест через API
docker compose exec api python3 -c "
import asyncio
from services.searxng_service import get_searxng_service

async def test():
    service = get_searxng_service()
    result = await service.search('test', user_id='test')
    print(f'Results: {len(result.results)}')

asyncio.run(test())
"
```

## 🔗 Ссылки

- [n8n-installer SearXNG config](https://github.com/kossakovsky/n8n-installer/blob/main/Caddyfile)
- [SearXNG Documentation](https://docs.searxng.org/admin/settings.html)
- [Caddy Documentation](https://caddy.community/t/forwarded-headers-best-practices/10418)

