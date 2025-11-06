# SearXNG - Bot Detection все еще активен

**Дата**: 2025-02-02  
**Статус**: ⚠️ Bot Detection все еще активен, несмотря на `limiter: false`

## 🔍 Проблема

В логах SearXNG:
```
ERROR:searx.botdetection: X-Forwarded-For nor X-Real-IP header is set!
```

Это означает, что модуль `botdetection` все еще активен и проверяет заголовки, даже при `limiter: false` в settings.yml.

## 🔍 Причина

Согласно документации SearXNG и логам:
1. `limiter: false` отключает только rate limiting, но **не отключает bot detection**
2. Bot detection - это отдельный модуль, который требует отдельной настройки
3. Даже при `limiter: false`, botdetection может быть активен и требовать заголовки `X-Forwarded-For` или `X-Real-IP`

## ✅ Решение

### Вариант 1: Создать правильный limiter.toml (рекомендуется)

Согласно документации SearXNG, для полного отключения bot detection нужно создать файл `limiter.toml`:

```bash
sudo tee /opt/telegram-assistant/searxng/limiter.toml << 'EOF'
[botdetection]
enabled = false
EOF
```

**Важно**: 
- Файл должен быть доступен для чтения процессом SearXNG (обычно uid 991)
- Секция должна называться `[botdetection]` (не `[bot_detection]`)

После создания:
```bash
sudo chown 991:991 /opt/telegram-assistant/searxng/limiter.toml
sudo chmod 644 /opt/telegram-assistant/searxng/limiter.toml
docker compose --profile rag restart searxng
```

### Вариант 2: Добавить заголовки в каждый запрос (временное решение)

Заголовки `X-Forwarded-For` и `X-Real-IP` уже добавлены в `SearXNGService`, но возможно нужно использовать IP адрес контейнера вместо `127.0.0.1`.

## 📝 Текущая конфигурация

### settings.yml:
```yaml
server:
  limiter: false          # Отключает только rate limiting
  public_instance: false
```

### docker-compose.yml:
```yaml
environment:
  - SEARXNG_LIMITER=false  # Переопределяет limiter в settings.yml
```

### SearXNG Service:
```python
headers = {
    "Accept": "application/json",
    "X-Forwarded-For": "127.0.0.1",
    "X-Real-IP": "127.0.0.1"
}
```

## ⚠️ Важно

`limiter: false` **НЕ отключает bot detection**, только rate limiting. Для полного отключения bot detection нужен файл `limiter.toml` с `[botdetection] enabled = false`.

