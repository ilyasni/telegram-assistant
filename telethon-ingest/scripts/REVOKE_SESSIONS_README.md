# Отзыв активных Telegram сессий

## Описание

Скрипт `revoke_telegram_sessions.py` позволяет отозвать активные сессии Telegram для пользователя через Telegram API.

## Использование

```bash
# Отозвать все сессии кроме текущей (по умолчанию)
docker compose exec telethon-ingest python3 scripts/revoke_telegram_sessions.py <telegram_id>

# Отозвать все сессии включая текущую
docker compose exec telethon-ingest python3 scripts/revoke_telegram_sessions.py <telegram_id> --all

# Отозвать только текущую сессию
docker compose exec telethon-ingest python3 scripts/revoke_telegram_sessions.py <telegram_id> --current-only
```

## Примеры

```bash
# Отозвать все сессии кроме текущей для пользователя 8124731874
docker compose exec telethon-ingest python3 scripts/revoke_telegram_sessions.py 8124731874

# Отозвать все сессии включая текущую
docker compose exec telethon-ingest python3 scripts/revoke_telegram_sessions.py 8124731874 --all
```

## Требования

- Пользователь должен быть авторизован (иметь активную сессию в БД или Redis)
- Переменные окружения `MASTER_API_ID` и `MASTER_API_HASH` должны быть установлены

## Как это работает

1. Скрипт получает `session_string` из БД или Redis
2. Подключается к Telegram через Telethon
3. Получает список активных сессий через `GetAuthorizationsRequest`
4. Отзывает сессии через `ResetAuthorizationRequest`
5. Если отозвана текущая сессия, выполняет `log_out()`

## Важно

- Если пользователь разлогинен, скрипт выдаст ошибку "Client is not authorized"
- Для отзыва сессий нужна активная авторизованная сессия
- Отзыв сессий не удаляет данные пользователя из БД
