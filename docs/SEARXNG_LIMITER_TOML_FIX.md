# SearXNG - Создание limiter.toml

**Дата**: 2025-02-02

## ✅ Команды для выполнения

```bash
# 1. Создать limiter.toml
sudo tee /opt/telegram-assistant/searxng/limiter.toml << 'EOF'
[botdetection]
enabled = false

[limiter]
enabled = false
EOF

# 2. Установить права
sudo chown 977:977 /opt/telegram-assistant/searxng/limiter.toml
sudo chmod 644 /opt/telegram-assistant/searxng/limiter.toml

# 3. Проверить файл
cat /opt/telegram-assistant/searxng/limiter.toml

# 4. Перезапустить SearXNG
docker compose --profile rag restart searxng

# 5. Проверить логи
docker compose logs searxng --tail 20 | grep -iE "botdetection|limiter|error"

# 6. Тест
docker compose exec -T api python3 -c "
import asyncio
import sys
sys.path.insert(0, '/app')

async def test():
    from services.searxng_service import get_searxng_service
    service = get_searxng_service()
    result = await service.search('Python', user_id='test', lang='ru')
    print(f'Results: {len(result.results)}')

asyncio.run(test())
"
```

## 📋 Ожидаемый результат

После создания `limiter.toml`:
- ✅ Нет ошибок `TypeError: schema of /etc/searxng/limiter.toml is invalid!`
- ✅ Нет ошибок `X-Forwarded-For nor X-Real-IP header is set!`
- ✅ SearXNG возвращает 200 OK вместо 403 Forbidden

