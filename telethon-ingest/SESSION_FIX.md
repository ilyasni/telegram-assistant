# Исправление путей к файлам сессий Telegram

## Проблема

Файл `telegram_assistant.session` создавался в корне `telethon-ingest/` вместо директории `@sessions/`.

## Решение

### 1. Docker Compose Volume
Обновлён `docker-compose.yml`:
```yaml
volumes:
  - ./telethon-ingest/sessions:/app/sessions  # Сессии в sessions/
```

### 2. Использование StringSession
В примерах кода (`channel_parser.py`) заменены файловые сессии на `StringSession`:
```python
from telethon.sessions import StringSession
session = StringSession()  # Берется из БД через TelegramClientManager
client = TelegramClient(session, api_id, api_hash)
```

### 3. Утилита для путей
Создан `utils/session_path.py` для правильного формирования путей к файловым сессиям (если понадобятся).

## Best Practices (Context7)

1. **Используйте StringSession** вместо файловых сессий для production
2. **Храните сессии в БД** (зашифрованные через `crypto_utils`)
3. **Используйте TelegramClientManager** для управления клиентами
4. **Не храните сессии в Git** - используйте `.gitignore`

## Миграция

Существующий файл `telegram_assistant.session` должен быть в `sessions/`:
```bash
# С правами sudo (если нужно)
sudo mv telethon-ingest/telegram_assistant.session telethon-ingest/sessions/

# Или скопировать в контейнер
docker compose exec telethon-ingest sh -c "mkdir -p /app/sessions && cp /app/telegram_assistant.session /app/sessions/"
```

## Проверка

```bash
# Проверить наличие файлов сессий на хосте
ls -la telethon-ingest/sessions/*.session

# В контейнере
docker compose exec telethon-ingest ls -la /app/sessions/
```

