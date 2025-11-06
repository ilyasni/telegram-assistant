# SearXNG - Правильный формат limiter.toml

**Дата**: 2025-02-02

## ✅ Решение

Согласно документации SearXNG, правильный формат `limiter.toml`:

```toml
[botdetection]
ipv4_prefix = 32
ipv6_prefix = 48
trusted_proxies = [
  '127.0.0.0/8',
  '::1',
  '172.18.0.0/16',  # Docker подсеть
  '172.16.0.0/12',
  '10.0.0.0/8',
]

[botdetection.ip_limit]
filter_link_local = false
link_token = false

[botdetection.ip_lists]
block_ip = []
pass_ip = [
  '0.0.0.0/0',      # Разрешаем все IP
  '127.0.0.1/32',   # localhost
  '172.18.0.0/16',  # Docker подсеть
]
pass_searxng_org = true
```

## 📋 Команды для создания

```bash
# 1. Создать limiter.toml
sudo tee /opt/telegram-assistant/searxng/limiter.toml << 'EOF'
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
EOF

# 2. Установить права
sudo chown 977:977 /opt/telegram-assistant/searxng/limiter.toml
sudo chmod 644 /opt/telegram-assistant/searxng/limiter.toml

# 3. Перезапустить SearXNG
docker compose --profile rag restart searxng

# 4. Проверить логи
docker compose logs searxng --tail 30 | grep -iE "botdetection|limiter|error"
```

## ⚠️ Важно

- Формат должен соответствовать схеме SearXNG
- Все секции обязательны: `[botdetection]`, `[botdetection.ip_limit]`, `[botdetection.ip_lists]`
- `pass_ip` должен быть списком строк
- Права доступа: `977:977` (или другой uid/gid контейнера)

## ✅ Проверка

После создания файла:
- ✅ Нет ошибок `TypeError: schema of /etc/searxng/limiter.toml is invalid!`
- ✅ SearXNG запускается без ошибок
- ✅ SearXNG возвращает 200 OK вместо 403 Forbidden

