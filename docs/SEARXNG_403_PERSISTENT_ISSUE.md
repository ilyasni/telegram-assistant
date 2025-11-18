# SearXNG - Постоянная проблема 403 Forbidden

**Дата**: 2025-02-02

## ❌ Проблема

SearXNG продолжает возвращать `403 Forbidden` даже после:
- ✅ Создания правильного `limiter.toml` с корректной схемой
- ✅ Установки `server.limiter: false` в settings.yml
- ✅ Установки `SEARXNG_LIMITER=false` в docker-compose.yml
- ✅ Настройки `botdetection.ip_lists.pass_ip` с `0.0.0.0/0` и `172.18.0.0/16`
- ✅ Передачи заголовков `X-Forwarded-For` и `X-Real-IP`

## 🔍 Анализ

### Текущая конфигурация

**limiter.toml:**
```toml
[botdetection]
ipv4_prefix = 32
ipv6_prefix = 48
trusted_proxies = [
  '127.0.0.0/8',
  '::1',
  '172.18.0.0/16',
]

[botdetection.ip_limit]
filter_link_local = false
link_token = false

[botdetection.ip_lists]
block_ip = []
pass_ip = [
  '0.0.0.0/0',
  '127.0.0.1/32',
  '172.18.0.0/16',
]
pass_searxng_org = true
```

**settings.yml:**
```yaml
server:
  limiter: false
  public_instance: false

botdetection:
  ip_lists:
    pass_ip:
      - "172.18.0.0/16"
      - "172.18.0.15/32"
      - "127.0.0.1/32"
```

**docker-compose.yml:**
```yaml
environment:
  - SEARXNG_LIMITER=false
  - SEARXNG_PUBLIC_INSTANCE=false
```

## 💡 Возможные причины

1. **Bot detection проверяет реальный source IP**, а не заголовки
2. **Конфигурация не применяется** из-за кэширования или порядка загрузки
3. **Версия SearXNG** может иметь баг с `pass_ip: ["0.0.0.0/0"]`
4. **Другие механизмы защиты**, не связанные с bot detection

## 🔧 Альтернативные решения

### Вариант 1: Использовать SearXNG через обратный прокси (Caddy)

Настроить Caddy для проксирования запросов к SearXNG с правильными заголовками:

```caddyfile
searxng.local {
    reverse_proxy searxng:8080 {
        header_up X-Real-IP {remote}
        header_up X-Forwarded-For {remote}
        header_up X-Forwarded-Proto {scheme}
    }
}
```

### Вариант 2: Отключить bot detection через код

Если возможно, отключить bot detection на уровне кода SearXNG (требует модификации образа).

### Вариант 3: Использовать внешний SearXNG инстанс

Использовать публичный SearXNG инстанс с BasicAuth (если доступен).

### Вариант 4: Проверить версию SearXNG

Обновить или откатить версию SearXNG, так как проблема может быть связана с конкретной версией.

## 📋 Диагностика

```bash
# 1. Проверить версию SearXNG
docker compose exec searxng env | grep SEARXNG_VERSION

# 2. Проверить логи подробнее
docker compose logs searxng --tail 200 | grep -iE "403|forbidden|bot|limiter"

# 3. Проверить локальный доступ
docker compose exec searxng wget -qO- "http://localhost:8080/search?q=test&format=json"

# 4. Проверить конфигурацию внутри контейнера
docker compose exec searxng cat /etc/searxng/settings.yml | grep -A10 "botdetection"
docker compose exec searxng cat /etc/searxng/limiter.toml
```

## ⚠️ Важно

Проблема может быть связана с:
- Специфической версией SearXNG
- Особенностями работы bot detection в Docker сети
- Необходимостью использования reverse proxy для правильной работы заголовков

## 📝 Рекомендация

Попробовать использовать SearXNG через Caddy reverse proxy, как это сделано в официальном репозитории [searxng-docker](https://github.com/searxng/searxng-docker).

