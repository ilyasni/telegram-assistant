# SearXNG - Конфигурация завершена

**Дата**: 2025-02-02  
**Источник**: https://github.com/kossakovsky/n8n-installer  
**Статус**: ✅ Конфигурация применена

## ✅ Применённые изменения (Context7 Best Practices)

### 1. Caddyfile - Полный домен с правильным синтаксисом

```caddyfile
searxng.produman.studio {
    # TLS с автоматическим получением сертификата (email из глобальных настроек)
    
    # Security headers
    header {
        Content-Security-Policy "..."
        Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
        X-Content-Type-Options "nosniff"
        -Server
    }
    
    # Правильный синтаксис Caddy из n8n-installer
    reverse_proxy searxng:8080 {
        header_up X-Forwarded-Port {http.request.port}
        header_up X-Real-IP {http.request.remote.host}  # Правильный синтаксис!
        header_up X-Forwarded-For {http.request.remote.host}
        header_up Connection "close"
    }
}
```

### 2. Docker Compose - SEARXNG_BASE_URL (КРИТИЧНО!)

```yaml
environment:
  - SEARXNG_BASE_URL=https://${SEARXNG_HOSTNAME:-searxng.produman.studio}/
  - SEARXNG_HOSTNAME=${SEARXNG_HOSTNAME:-searxng.produman.studio}
  - UWSGI_WORKERS=${SEARXNG_UWSGI_WORKERS:-4}
  - UWSGI_THREADS=${SEARXNG_UWSGI_THREADS:-4}
```

### 3. API Config - HTTPS URL

```python
searxng_url: str = "https://searxng.produman.studio"
```

### 4. SearXNG Service - Отключение проверки SSL

```python
self.http_client = httpx.AsyncClient(
    verify=False  # Для внутреннего использования через Caddy
)
```

### 5. Settings.yml - use_default_settings

```yaml
use_default_settings: true
server:
  limiter: false
  public_instance: false
  image_proxy: true
```

## 📋 Требуется настройка

### 1. Переменные окружения (.env)

Добавить в `.env`:
```bash
SEARXNG_HOSTNAME=searxng.produman.studio
SEARXNG_BASE_URL=https://searxng.produman.studio/
```

### 2. DNS настройка

Добавить A-запись в DNS:
```
searxng.produman.studio → 193.201.88.88
```

### 3. Перезапуск

После настройки DNS:
```bash
docker compose --profile rag restart searxng
docker compose --profile core restart caddy
```

## ✅ Преимущества новой конфигурации

1. ✅ **Правильный синтаксис Caddy** - `{http.request.remote.host}` из n8n-installer
2. ✅ **SEARXNG_BASE_URL** - критически важно для работы bot detection
3. ✅ **HTTPS через Caddy** - автоматический TLS
4. ✅ **Security headers** - полный набор заголовков безопасности
5. ✅ **Cache policy** - оптимизированное кэширование
6. ✅ **Production-ready** - готово для продакшена

## 🔗 Ссылки

- [n8n-installer SearXNG config](https://github.com/kossakovsky/n8n-installer/blob/main/Caddyfile)
- [SearXNG Documentation](https://docs.searxng.org/admin/settings.html)
- [Caddy Documentation](https://caddyserver.com/docs/)

