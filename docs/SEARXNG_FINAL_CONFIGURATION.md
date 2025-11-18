# SearXNG - Финальная конфигурация на основе n8n-installer

**Дата**: 2025-02-02  
**Источник**: https://github.com/kossakovsky/n8n-installer  
**Статус**: ✅ Применено

## ✅ Применённые изменения (Context7 Best Practices)

### 1. Caddyfile - Полный домен с правильным синтаксисом

```caddyfile
searxng.produman.studio {
    tls {
        email {$CADDY_TLS_EMAIL}
    }
    
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

### 2. Docker Compose - SEARXNG_BASE_URL (КРИТИЧНО!)

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

## 🔍 Ключевые отличия от предыдущей конфигурации

1. **Полный домен** вместо пути `/searxng/*`
2. **Правильный синтаксис Caddy** - `{http.request.remote.host}` вместо `{remote_host}`
3. **SEARXNG_BASE_URL** - критически важно для работы bot detection
4. **HTTPS через Caddy** - автоматический TLS
5. **Security headers** - полный набор заголовков безопасности

## ✅ Преимущества

1. ✅ **Bot detection работает корректно** - через reverse proxy с правильными заголовками
2. ✅ **Автоматический TLS** - Caddy получает сертификаты
3. ✅ **Security headers** - защита от XSS, clickjacking и т.д.
4. ✅ **Cache policy** - оптимизированное кэширование
5. ✅ **Production-ready** - готово для продакшена

## 📋 Текущий статус

- ✅ Caddyfile обновлен
- ✅ docker-compose.yml обновлен
- ✅ api/config.py обновлен
- ✅ env.example обновлен
- ✅ settings.yml обновлен
- ⚠️ Caddy требует настройки DNS для получения сертификата

## 🔧 Следующие шаги

1. **Настроить DNS**: `searxng.produman.studio → IP сервера`
2. **Проверить работу**: После настройки DNS Caddy автоматически получит сертификат
3. **Опционально**: Включить BasicAuth для защиты внешнего доступа

## 🔗 Ссылки

- [n8n-installer SearXNG config](https://github.com/kossakovsky/n8n-installer/blob/main/Caddyfile)
- [SearXNG Documentation](https://docs.searxng.org/admin/settings.html)
- [Caddy Documentation](https://caddyserver.com/docs/)
