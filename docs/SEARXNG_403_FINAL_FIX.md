# SearXNG - Финальное исправление 403 Forbidden

**Дата**: 2025-02-02

## 🔍 Диагностика

### Проверено:
1. ✅ Network: `172.18.0.0/16` - правильно
2. ✅ SearXNG IP: `172.18.0.6`
3. ✅ API IP: `172.18.0.15` (попадает в подсеть)
4. ✅ settings.yml: `botdetection.ip_lists.pass_ip: ["0.0.0.0/0"]` - временно для диагностики
5. ✅ settings.yml: `limiter: /etc/searxng/limiter.toml`
6. ✅ SearXNG Service: заголовки `X-Forwarded-For` и `X-Real-IP` добавлены

### Проблема:
Все еще **403 Forbidden**, даже с `pass_ip: "0.0.0.0/0"` и заголовками.

## ✅ Решение

### Создать limiter.toml на хосте

```bash
sudo tee /opt/telegram-assistant/searxng/limiter.toml << 'EOF'
[botdetection]
enabled = false

[limiter]
enabled = false
EOF

sudo chown 977:977 /opt/telegram-assistant/searxng/limiter.toml
sudo chmod 644 /opt/telegram-assistant/searxng/limiter.toml
```

### Или создать через контейнер (уже сделано):

```bash
docker compose exec -T searxng sh -c 'cat > /etc/searxng/limiter.toml << EOF
[botdetection]
enabled = false

[limiter]
enabled = false
EOF'
```

### Перезапустить SearXNG:

```bash
docker compose --profile rag restart searxng
```

## 📋 Текущая конфигурация

### settings.yml:
```yaml
server:
  limiter: /etc/searxng/limiter.toml

botdetection:
  ip_lists:
    pass_ip:
      - "0.0.0.0/0"  # Временно для диагностики
```

### limiter.toml:
```toml
[botdetection]
enabled = false

[limiter]
enabled = false
```

### SearXNG Service:
- Заголовки `X-Forwarded-For` и `X-Real-IP` добавлены в http_client
- IP контейнера определяется автоматически через `socket.gethostbyname()`

## ⚠️ Если все еще 403

Проверить:
1. Логи SearXNG: `docker compose logs searxng --tail 50 | grep -iE "botdetection|403|error"`
2. Существование limiter.toml: `docker compose exec searxng ls -la /etc/searxng/limiter.toml`
3. Содержимое limiter.toml: `docker compose exec searxng cat /etc/searxng/limiter.toml`

