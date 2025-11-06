# SearXNG - Удаление limiter.toml

**Дата**: 2025-02-02

## ❌ Проблема

При попытке создать `limiter.toml` с простым форматом:
```toml
[botdetection]
enabled = false

[limiter]
enabled = false
```

SearXNG выдает ошибку:
```
TypeError: schema of /etc/searxng/limiter.toml is invalid!
```

## ✅ Решение

**Вариант 1: Удалить limiter.toml (рекомендуется)**

Поскольку в `settings.yml` уже установлено:
- `server.limiter: false`
- `botdetection.ip_lists.pass_ip: ["0.0.0.0/0"]`

Файл `limiter.toml` не требуется. Удалите его:

```bash
sudo rm -f /opt/telegram-assistant/searxng/limiter.toml
docker compose --profile rag restart searxng
```

**Вариант 2: Создать пустой limiter.toml (если SearXNG требует его наличия)**

```bash
sudo touch /opt/telegram-assistant/searxng/limiter.toml
sudo chown 977:977 /opt/telegram-assistant/searxng/limiter.toml
sudo chmod 644 /opt/telegram-assistant/searxng/limiter.toml
docker compose --profile rag restart searxng
```

## 📋 Текущая конфигурация

### settings.yml
```yaml
server:
  limiter: false
  public_instance: false
  method: "GET"

botdetection:
  ip_lists:
    pass_ip:
      - "0.0.0.0/0"
```

### docker-compose.yml
```yaml
environment:
  - SEARXNG_LIMITER=false
  - SEARXNG_PUBLIC_INSTANCE=false
```

## ⚠️ Важно

- `botdetection.enabled: false` **не поддерживается** в `settings.yml`
- Для отключения bot detection используйте `pass_ip: ["0.0.0.0/0"]` в `botdetection.ip_lists`
- `limiter.toml` имеет сложную схему и не может быть создан вручную без знания точной структуры

## ✅ Итог

После удаления `limiter.toml`:
- SearXNG использует настройки из `settings.yml`
- `limiter: false` отключает rate limiting
- `pass_ip: ["0.0.0.0/0"]` разрешает все IP адреса
- SearXNG должен работать без 403 ошибок

